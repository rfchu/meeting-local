"""Incremental, recording-bound meeting drafts. Inference never owns the ASR lock."""
from .resources import optional_slot,ResourceBusy
import hashlib,json,re,subprocess,sys,tempfile,threading,time
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field,ValidationError
from .analysis import OpenAICompatibleProvider
from .db import encode,uid
from .live_results import plan_ranges,RATE
from .terminology import confirmed_context, rows as terminology_rows
from .overview_quality import same_item,select_items
from .overview_prompt import INSTRUCTION,PROMPT_VERSION

BASE=Path(__file__).resolve().parents[1]
INTERVAL=45
MAX_ITEMS=20

def generation_timeout(profile):
    # 3072 output tokens can exceed 45s on a local 27B model, even without reasoning.
    return 180 if profile['provider_type']=='local' else 120

def worker_failure(log):
    # Never expose prompts, server response bodies, keys, or raw tracebacks.
    if re.search(r'HTTP Error (401|403)',log):return '模型服务认证失败，请检查 API Key'
    if 'HTTP Error 404' in log:return '模型服务地址或模型不可用，请检查模型配置'
    if 'HTTP Error 400' in log or 'HTTP Error 422' in log:return '模型服务拒绝了请求，请检查模型及接口兼容性'
    if 'Connection refused' in log or 'URLError' in log:return '无法连接模型服务，请确认服务已启动'
    if '模型连续返回不完整' in log or 'JSONDecodeError' in log:return '模型未返回完整 JSON，请换用支持结构化输出的模型或重试'
    return '模型调用失败，请检查模型服务后重试'


TOPIC_ARCHIVE_LIMIT = 32


def topic_snapshots(db, rid):
    """Return the newest retained snapshot for each stable topic identity."""
    return db.execute("""
        WITH candidates AS (
          SELECT s.version, json_extract(item.value,'$.id') AS id,
                 json_extract(item.value,'$.content') AS title,
                 json_extract(item.value,'$.evidence[0].start') AS start,
                 row_number() OVER (
                   PARTITION BY json_extract(item.value,'$.id') ORDER BY s.version DESC
                 ) AS occurrence
          FROM live_overview_snapshots s, json_each(s.payload,'$.items') item
          WHERE s.recording_id=? AND json_extract(item.value,'$.kind')='topic'
            AND json_extract(item.value,'$.id') IS NOT NULL
        )
        SELECT version,id,title,start FROM candidates WHERE occurrence=1
        ORDER BY version DESC LIMIT ?
    """, (rid, TOPIC_ARCHIVE_LIMIT)).fetchall()


def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()

class Evidence(BaseModel):
    segment_id:str=Field(min_length=1,max_length=160)
    quote:str=Field(min_length=2,max_length=400)

class OverviewItem(BaseModel):
    id:str|None=Field(default=None,max_length=100)
    kind:Literal['topic','point','decision','question','action']
    content:str=Field(min_length=1,max_length=350)
    evidence:list[Evidence]=Field(min_length=1,max_length=4)
    note_ids:list[str]=Field(default_factory=list,max_length=4)
    topic_ref:str|None=Field(default=None,max_length=100)

class TermCandidate(BaseModel):
    original:str=Field(min_length=2,max_length=80)
    name:str=Field(min_length=2,max_length=80)
    category:Literal['person','organization','product','technical','other']
    evidence_id:str=Field(max_length=40)


class OverviewOutput(BaseModel):
    filtered_count:int=Field(default=0,ge=0)
    terms:list[dict]=Field(default_factory=list,max_length=8)
    items:list[OverviewItem]=Field(max_length=MAX_ITEMS)

class ModelItem(BaseModel):
    id:str|None=Field(default=None,max_length=100)
    kind:Literal['topic','point','decision','question','action']
    content:str=Field(min_length=1,max_length=350)
    evidence_ids:list[str]=Field(min_length=1,max_length=4)
    note_ids:list[str]=Field(default_factory=list,max_length=4)
    topic_ref:str|None=Field(max_length=100)

class ModelOutput(BaseModel):
    items:list[ModelItem]=Field(max_length=MAX_ITEMS)
    terms:list[TermCandidate]=Field(default_factory=list,max_length=8)


def numbered_input(data):
    """The model selects server-owned quote IDs, never writes source quotations."""
    sources={p['id']:p for p in data['context']+data['new_segments']}
    quotes={};lookup={}
    def add(sid,quote):
        key=(sid,quote)
        if key not in lookup:
            eid='e'+str(len(quotes)+1);lookup[key]=eid
            quotes[eid]={'segment_id':sid,'quote':quote}
        return lookup[key]
    topic_aliases={topic['id']:f't{n+1}' for n,topic in enumerate(data.get('agenda_topics',[]))}
    current=[]
    for item in data['current_items']:
        ids=[add(e['segment_id'],e['quote']) for e in item['evidence'] if e['segment_id'] in sources and e['quote'] in sources[e['segment_id']]['text']]
        current.append({k:item[k] for k in ('id','kind','content','note_ids')}|{'evidence_ids':ids,'topic_ref':topic_aliases.get(item.get('topic_ref') or item['id'])})
    item_aliases={item['id']:'i'+str(n+1) for n,item in enumerate(current)}
    note_aliases={note['id']:'n'+str(n+1) for n,note in enumerate(data['focus_notes'])}
    for item in current:
        item['id']=item_aliases[item['id']]
        item['note_ids']=[note_aliases[nid] for nid in item['note_ids'] if nid in note_aliases]
    new_ids={p['id'] for p in data['new_segments']}
    for sid,part in sources.items():
        # Sentence anchors stay exact substrings. Long sentences are bounded without paraphrasing.
        for sentence in re.findall(r'[^。！？\n]+[。！？]?|[。！？]',part['text']):
            for start in range(0,len(sentence),240):
                quote=sentence[start:start+240].strip()
                if len(quote)>=2:add(sid,quote)
    model={k:v for k,v in data.items() if k not in ('context','new_segments','current_items','dismissed_items')}
    model['focus_notes']=[dict(note,id=note_aliases[note['id']]) for note in data['focus_notes']]
    model['agenda_topics']=[{'ref':topic_aliases[t['id']],'title':t['content']} for t in data.get('agenda_topics',[])]
    model['current_items']=current
    model['dismissed_items']=[{k:i[k] for k in ('kind','content')} for i in data['dismissed_items']]
    model['evidence']=[{'id':eid,'text':e['quote'],'start':sources[e['segment_id']]['start'],
        'new':e['segment_id'] in new_ids} for eid,e in quotes.items()]
    return model,quotes


def expand_numbered(result,quotes,item_ids=None,note_ids=None,topic_ids=None):
    # Keep the schema's four-reference limit, but tolerate extra valid IDs
    # from compatible providers that do not enforce the requested schema.
    items=[];filtered=0
    raw_items=result.get('items') if isinstance(result,dict) else None
    if not isinstance(raw_items,list):
        raise ValueError('概览格式不完整，已保留原文和上一版')
    for raw in raw_items[:MAX_ITEMS]:
        if not isinstance(raw,dict):
            filtered+=1;continue
        ids=raw.get('evidence_ids')
        if not isinstance(ids,list) or not ids or any(not isinstance(eid,str) or eid not in quotes for eid in ids):
            filtered+=1;continue
        try:
            item=ModelItem.model_validate({'topic_ref':None,**raw,'evidence_ids':list(dict.fromkeys(ids))[:4]})
        except ValidationError:
            filtered+=1;continue
        value=item.model_dump(exclude={'evidence_ids'})
        if item_ids is not None and value['id'] is not None:
            # A model-created ID cannot overwrite a real item; treat it as a new candidate.
            value['id']=item_ids.get(value['id'])
        if note_ids is not None:
            value['note_ids']=[note_ids[nid] for nid in value['note_ids'] if nid in note_ids]
        if topic_ids is not None:
            ref=item.topic_ref or ''
            value['topic_ref']=topic_ids.get(ref) or (ref if re.fullmatch(r'new_[1-9]',ref) else None)
        value['evidence']=[quotes[eid] for eid in dict.fromkeys(item.evidence_ids)]
        items.append(value)
    if raw_items and not items:raise ValueError('本轮候选均缺少有效证据编号，上一版已保留')
    terms=[]
    candidates=result.get('terms',[])
    for raw in candidates[:8] if isinstance(candidates,list) else []:
        try:term=TermCandidate.model_validate(raw)
        except ValidationError:continue
        evidence=quotes.get(term.evidence_id)
        if evidence and term.original in evidence['quote']:
            terms.append(term.model_dump(exclude={'evidence_id'})|{'evidence':evidence})
    return OverviewOutput.model_validate({'items':items,'filtered_count':filtered,'terms':terms})


class EnableInput(BaseModel):
    profile_id:str=Field(min_length=1,max_length=100)
    engine:Literal['qwen','whisper']='qwen'
    confirm_cloud:bool=False
    expected_provider:dict[str,str]|None=None

class ReviewInput(BaseModel):
    state:Literal['confirmed','dismissed','pending']
    fingerprint:str=Field(min_length=64,max_length=64)
    version:int=Field(ge=0)
    content:str|None=Field(default=None,min_length=1,max_length=350)

class Deferred(Exception):pass


def item_core(item):
    return {key:item[key] for key in ('id','kind','content','evidence','note_ids')}|({'note_fingerprints':item['note_fingerprints']} if 'note_fingerprints' in item else {})|({'topic_ref':item['topic_ref']} if item.get('topic_ref') else {})


def update_agenda(previous, incoming):
    """Keep the latest version of each grounded item across overview rounds."""
    values={item['id']:item for item in previous}
    for item in incoming:
        prior=values.get(item['id'],{})
        if item['kind']=='topic':
            item={**item,'topic_start':prior.get('topic_start',min(e['start'] for e in item['evidence']))}
        if item['kind']=='decision' and prior.get('kind')=='decision':
            previous=prior.get('previous_decision')
            if prior['content']!=item['content'] and {
                e['segment_id'] for e in item['evidence']
            } - {e['segment_id'] for e in prior['evidence']}:
                previous={'content':prior['content'],'evidence':prior['evidence']}
            if previous:item={**item,'previous_decision':previous}
        values[item['id']]=item
    topics=[item for item in values.values() if item['kind']=='topic']
    topics=sorted(topics,key=lambda item:max(e['start'] for e in item['evidence']))[-TOPIC_ARCHIVE_LIMIT:]
    groups={item['id']:[] for item in topics}
    unassigned=[]
    for item in values.values():
        if item['kind']=='topic':continue
        if item.get('topic_ref') in groups:
            groups[item['topic_ref']].append(item)
        elif not item.get('topic_ref'):
            unassigned.append(item)
    def latest(items):
        return sorted(items,key=lambda item:max(e['start'] for e in item['evidence']))[-MAX_ITEMS:]
    return topics+[item for group in groups.values() for item in latest(group)]+latest(unassigned)


def matching_agenda_topic(plan):
    """Use a clear lexical match as a backstop for a single-topic update."""
    def anchors(text):
        words=re.findall(r'[\u3400-\u9fff]+|[a-zA-Z][a-zA-Z0-9-]{3,}',text.casefold())
        return {part for word in words for part in (
            [word] if word.isascii() else [word[n:n+3] for n in range(len(word)-2)]
        )}

    incoming=anchors(' '.join(part['text'] for part in plan['input']['new_segments']))
    sources={part['id']:part['text'] for part in plan['parts']}
    scores={topic['id']:set() for topic in plan.get('agenda_topics',[])}
    for item in plan.get('agenda',[]):
        ref=item['id'] if item['kind']=='topic' else item.get('topic_ref')
        if ref in scores:
            scores[ref].update(incoming & anchors(' '.join(
                sources.get(entry['segment_id'],'') for entry in item['evidence']
            )))
    if len(scores)<2:return None
    ranked=sorted(scores,key=lambda key:len(scores[key]),reverse=True)
    best,other=ranked[:2]
    if len(scores[best])>=6 and len(scores[best])>=2*max(1,len(scores[other])):
        return best
    return None


def evidence_valid(item,sources):
    return bool(item.get('evidence')) and all(e['segment_id'] in sources and e['quote'] in sources[e['segment_id']]['text'] for e in item['evidence'])


def focus_notes(notes):
    result=[]
    for index,note in enumerate(notes):
        if not (note.get('note_scope')=='meeting' or note.get('overview_focus')) or not note.get('raw_text','').strip():continue
        result.append({'id':note.get('client_id') or 'note-'+digest([index,note['captured_at'],note['raw_text']])[:24],
            'time':float(note['captured_at']),'type':note['note_type'],'text':note['raw_text'].strip(),
            'scope':note.get('note_scope','personal'),'explicit_focus':bool(note.get('overview_focus'))})
    return result


def approved_context(db,mid,segments):
    """Carry human priorities only when each raw quote maps uniquely to current transcript."""
    result=[]
    run=db.execute('SELECT config FROM live_overview_runs WHERE recording_id=?',(mid,)).fetchone()
    if not run:return result
    engine=json.loads(run['config'])['engine'];ranges=[];seen=set()
    for row in db.execute("SELECT * FROM live_asr_ranges WHERE recording_id=? AND engine=? AND state='completed' ORDER BY rowid DESC",(mid,engine)):
        key=(row['start_frame'],row['end_frame'])
        if key not in seen:seen.add(key);ranges.append(dict(row))
    selected,_=plan_ranges(ranges,max((r['end_frame']/RATE for r in ranges),default=0))
    selected_ids={r['id'] for r in selected}
    for row in db.execute("SELECT payload FROM live_overview_reviews WHERE recording_id=? AND state='confirmed' ORDER BY item_id",(mid,)):
        item=json.loads(row['payload']);matched=[]
        note_rows=[dict(r) for r in db.execute('SELECT * FROM meeting_notes WHERE meeting_id=?',(mid,))]
        known={n['id']:digest(n) for n in focus_notes(note_rows)}
        if any(known.get(key)!=value for key,value in item.get('note_fingerprints',{}).items()):continue
        for evidence in item['evidence']:
            # Source timestamps come from the saved, verified raw range, never from the LLM.
            raw=db.execute("SELECT segments FROM live_asr_ranges WHERE id=? AND recording_id=? AND state='completed'",(evidence.get('range_id'),mid)).fetchone()
            if not raw or evidence.get('range_id') not in selected_ids:matched=[];break
            raw_parts=json.loads(raw['segments']);index=evidence.get('segment_index',-1)
            if not 0<=index<len(raw_parts):matched=[];break
            part=raw_parts[index]
            from .live_corrections import apply_edits
            for correction in db.execute("SELECT * FROM live_corrections WHERE range_id=? AND segment_index=? AND state='accepted' ORDER BY created",(evidence.get('range_id'),index)):
                if part['text']==correction['original_text']:part=dict(part,text=apply_edits(part['text'],json.loads(correction['edits'])))
            if evidence['quote'] not in part['text']:matched=[];break
            start,end=part['start'],part['end']
            candidates=[s for s in segments if s['start']<end+.2 and s['end']>start-.2 and evidence['quote'] in s['text']]
            if len(candidates)!=1:matched=[];break
            matched.append({'segment_id':candidates[0]['id'],'quote':evidence['quote']})
        if matched:result.append({'item_id':item['id'],'kind':item['kind'],'content':item['content'],
            'source_segment_ids':list(dict.fromkeys(e['segment_id'] for e in matched)),'evidence':matched,
            'status':'human_confirmed_priority_not_independent_fact'})
    if len(encode(result))>12000:raise ValueError('已确认会中概览超过整理预算，请减少后再生成纪要')
    return result


class LiveOverview:
    def __init__(self,store,recordings,cache):
        self.resources=None
        self.store=store;self.recordings=recordings;self.cache=cache
        self.lock=threading.Lock();self.closed=threading.Event();self.thread=None;self.generator=None

    def session(self,rid):
        return self.recordings.read(rid)

    def source(self,rid,engine):
        ranges=self.cache.snapshot(rid,engine)
        duration=max((r['end_frame']/RATE for r in ranges),default=0)
        selected,gaps=plan_ranges(ranges,duration)
        parts=[]
        for row in selected:
            for index,segment in enumerate(row['segments']):
                if not segment['text'].strip():continue
                parts.append({'id':row['id']+':'+str(index),'text':segment['text'],'start':segment['start'],'end':segment['end'],
                    'range_id':row['id'],'segment_index':index})
        return parts,gaps

    def meta(self,rid,session):
        stored=self.store.all('SELECT * FROM meeting_notes WHERE meeting_id=?',(rid,)) if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)) else session.get('notes',[])
        notes=focus_notes(stored)
        if len({n['id'] for n in notes})!=len(notes):raise ValueError('简记标识重复，请刷新录音页面后重试')
        if len(notes)>30 or sum(len(n['text']) for n in notes)>6000:raise ValueError('选入概览的简记过多，请减少后继续')
        with self.store.connect() as db:terms=confirmed_context(db,rid)
        return {'preparation':session.get('brief') or {},'focus_notes':notes,'confirmed_terminology':terms}

    def latest(self,rid):
        return self.store.one('SELECT * FROM live_overview_snapshots WHERE recording_id=? ORDER BY version DESC LIMIT 1',(rid,))

    def reviews(self,rid):
        return {r['item_id']:dict(r,payload=json.loads(r['payload'])) for r in self.store.all('SELECT * FROM live_overview_reviews WHERE recording_id=?',(rid,))}

    def effective(self,rid,snapshot):
        values={i['id']:i for i in json.loads(snapshot['payload'])['items']} if snapshot else {}
        for key,row in self.reviews(rid).items():
            if row['state']!='pending' or key not in values:values[key]={**row['payload'],'decision':row['state']}
        return list(values.values())

    def enable(self,rid,body):
        with self.recordings.lock:
            session=self.session(rid)
            if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):raise ValueError('会议已保存，会中概览仅供回看；请在会议成果中整理正式纪要')
            profile=self.store.one('SELECT * FROM llm_configs WHERE id=?',(body.profile_id,))
            if not profile:raise ValueError('请选择已配置的 LLM 服务')
            provider=OpenAICompatibleProvider(profile);model=provider.resolve_model()
            expected={key:profile[key] for key in ('id','provider_type','base_url','model')}
            if profile['provider_type']=='cloud' and (not body.confirm_cloud or body.expected_provider!=expected):raise ValueError('请核对云端地址与模型，并确认发送本场后续确认文字、会议准备及明确选入的简记')
            config={'profile_id':profile['id'],'provider':expected,'name':profile['name'],'model':model,'engine':body.engine,'cloud_confirmed':body.confirm_cloud,'consent_scope':'live-overview-v1'}
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT config,enabled FROM live_overview_runs WHERE recording_id=?',(rid,)).fetchone()
                if old and old['enabled']:
                    if json.loads(old['config'])!=config:raise ValueError('请先暂停概览，再更换模型')
                    return self.read(rid)
                db.execute("INSERT INTO live_overview_runs(recording_id,config) VALUES(?,?) ON CONFLICT(recording_id) DO UPDATE SET config=excluded.config,enabled=1,epoch=epoch+1,state='waiting',next_due=0,force=0,failures=0,error='',updated=CURRENT_TIMESTAMP",(rid,encode(config)))
        return self.read(rid)

    def disable(self,rid):
        self.store.execute("UPDATE live_overview_runs SET enabled=0,epoch=epoch+1,state='disabled',updated=CURRENT_TIMESTAMP WHERE recording_id=?",(rid,))
        return self.read(rid)

    def read(self,rid,version=None):
        self.session(rid)
        run=self.store.one('SELECT * FROM live_overview_runs WHERE recording_id=?',(rid,))
        snapshot=self.store.one('SELECT * FROM live_overview_snapshots WHERE recording_id=? AND version=?',(rid,version)) if version else self.latest(rid)
        if version and not snapshot:raise ValueError('此概览快照已清理')
        config=json.loads(run['config']) if run else None
        parts,gaps=self.source(rid,config['engine']) if config else ([],[]);sources={s['id']:s for s in parts}
        payload=json.loads(snapshot['payload']) if snapshot else {}
        items=payload['items'] if version else self.effective(rid,snapshot)
        agenda=payload.get('agenda',[])
        item_ids={item['id'] for item in items}
        candidates=items+[item for item in agenda if item['id'] not in item_ids]
        decisions=self.reviews(rid)
        stored=self.store.all('SELECT * FROM meeting_notes WHERE meeting_id=?',(rid,)) if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)) else self.session(rid).get('notes',[])
        note_hashes={n['id']:digest(n) for n in focus_notes(stored)}
        values=[]
        for item in candidates:
            row=decisions.get(item['id'])
            if row and not version and row['state']!='pending':
                item={**item,**row['payload']}
            stale_notes=any(note_hashes.get(k)!=v for k,v in item.get('note_fingerprints',{}).items())
            evidence=[]
            for entry in item['evidence']:
                source=sources.get(entry['segment_id'])
                evidence.append({**entry,'start':source['start'] if source else entry.get('start',0),
                                 'stale':not source or entry['quote'] not in source['text']})
            stale_sources=not evidence or any(entry['stale'] for entry in evidence)
            value={**item,'decision':item.get('decision','pending') if version else row['state'] if row else 'pending',
                   'review_version':row['version'] if row else 0,'stale':stale_sources or stale_notes,
                   'stale_reasons':[name for name,stale in [('source',stale_sources),('note',stale_notes)] if stale],
                   'fingerprint':digest(item_core(item)),'evidence':evidence}
            if row:
                value['review_trace']={'state':row['state'],'updated':row['updated'],'version':row['version'],
                                       'snapshot_version':row['payload'].get('reviewed_snapshot_version')}
            values.append(value)
        rendered={item['id']:item for item in values}
        agenda_view=[]
        for item in agenda:
            value={**item,**rendered[item['id']]}
            previous=item.get('previous_decision')
            if previous and evidence_valid(previous,sources):
                value['previous_decision']=previous
            else:
                value.pop('previous_decision',None)
            agenda_view.append(value)
        terms=[]
        with self.store.connect() as db:
            known_names={name.casefold() for term in terminology_rows(db,rid,'confirmed')
                         for name in [term['name'],*term['aliases']]}
        for term in payload.get('terms',[]):
            if term['original'].casefold() in known_names:continue
            evidence=term['evidence'];source=sources.get(evidence['segment_id'])
            if source and evidence['quote'] in source['text']:
                terms.append({**term,'evidence':{**evidence,'start':source['start']}})
        history=self.store.all('SELECT version,cursor,created FROM live_overview_snapshots WHERE recording_id=? ORDER BY version DESC LIMIT 12',(rid,))
        with self.store.connect() as db:
            topics=[dict(row) for row in topic_snapshots(db,rid)]
        return {'enabled':bool(run and run['enabled']),'state':run['state'] if run else 'unconfigured','error':run['error'] if run else '',
            'failures':run['failures'] if run else 0,
            'elapsed_seconds':round(max(0,time.time()-run['last_attempt'])) if run and run['state']=='running' else 0,
            'retry_in_seconds':round(max(0,run['next_due']-time.time())) if run and run['enabled'] and run['state']=='failed' else 0,
            'timeout_seconds':generation_timeout(config['provider']) if config else 0,
            'confirmed_until':max((p['end'] for p in parts),default=0),
            'consent_scope':config.get('consent_scope') if config else None,
            'provider':{key:config[key] for key in ('name','model','engine')}|config['provider'] if config else None,
            'snapshot_provider':json.loads(snapshot['payload']).get('provider') if snapshot else None,
            'filtered_count':json.loads(snapshot['payload']).get('filtered_count',0) if snapshot else 0,
            'quality':json.loads(snapshot['payload']).get('quality',{}) if snapshot else {},
            'version':snapshot['version'] if snapshot else 0,'updated':snapshot['created'] if snapshot else None,
            'covered_count':snapshot['cursor'] if snapshot else 0,'confirmed_count':len(parts),'gap_count':len(gaps),
            'items':[item for item in values if item['id'] in item_ids],
            'agenda':agenda_view,
            'agenda_terms':terms,'covered_until':payload.get('covered_until'),
            'history':history,'topics':topics,'latest_version':history[0]['version'] if history else 0,'historical':bool(version),'published':bool(self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)))}

    def refresh(self,rid):
        run=self.store.one('SELECT * FROM live_overview_runs WHERE recording_id=?',(rid,))
        if not run or not run['enabled']:raise ValueError('请先开启概览')
        if self.session(rid)['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):raise ValueError('录音已结束，请整理正式纪要')
        if run['state']=='running':raise ValueError('本轮仍在生成，请等待结果或先暂停概览')
        if run['state']!='failed' and time.time()-run['last_attempt']<15:raise ValueError('上一轮刚开始，请稍后再更新')
        self.store.execute("UPDATE live_overview_runs SET force=1,next_due=0 WHERE recording_id=?",(rid,))
        return self.read(rid)

    def decide(self,rid,item_id,body):
        with self.recordings.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            snapshot=db.execute('SELECT * FROM live_overview_snapshots WHERE recording_id=? ORDER BY version DESC LIMIT 1',(rid,)).fetchone()
            if not snapshot:raise ValueError('尚无概览可核对')
            old=db.execute('SELECT * FROM live_overview_reviews WHERE recording_id=? AND item_id=?',(rid,item_id)).fetchone()
            payload=json.loads(snapshot['payload'])
            items={i['id']:i for i in payload.get('agenda',[])+payload['items']}
            if old and old['state']!='pending':items[item_id]=json.loads(old['payload'])
            item=items.get(item_id)
            if not item or digest(item_core(item))!=body.fingerprint or (old['version'] if old else 0)!=body.version:raise ValueError('概览条目已变化，请刷新后再核对')
            run=db.execute('SELECT config FROM live_overview_runs WHERE recording_id=?',(rid,)).fetchone()
            parts,_=self.source(rid,json.loads(run['config'])['engine']);sources={s['id']:s for s in parts}
            if body.state=='confirmed' and (not evidence_valid(item,sources) or any({n['id']:digest(n) for n in self.meta(rid,self.session(rid))['focus_notes']}.get(k)!=v for k,v in item.get('note_fingerprints',{}).items())):raise ValueError('原文依据已变化，请重新整理或回听核对')
            if body.content is not None:
                if body.state!='confirmed' or not body.content.strip():raise ValueError('手动修改后需要确认保存')
                item={**item,'content':body.content.strip()}
            if body.state=='confirmed' and not(old and old['state']=='confirmed') and db.execute("SELECT COUNT(*) FROM live_overview_reviews WHERE recording_id=? AND state='confirmed'",(rid,)).fetchone()[0]>=20:raise ValueError('最多确认20条，请先撤回部分条目')
            db.execute('INSERT INTO live_overview_reviews(recording_id,item_id,state,payload) VALUES(?,?,?,?) ON CONFLICT(recording_id,item_id) DO UPDATE SET state=excluded.state,payload=excluded.payload,version=version+1,updated=CURRENT_TIMESTAMP',
                (rid,item_id,body.state,encode(item_core(item)|{'reviewed_snapshot_version':snapshot['version']})))
        return self.read(rid)

    def prepare(self,rid,run,forced=False):
        config=json.loads(run['config']);session=self.session(rid)
        if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):
            self.store.execute("UPDATE live_overview_runs SET enabled=0,epoch=epoch+1,state='finished' WHERE recording_id=?",(rid,));return None
        if time.time()-session.get('updated',session['created'])>120:raise Deferred('录音连接暂未同步，等待恢复')
        if session.get('source')=='macos':
            path=self.recordings.folder(rid)/'capture.json'
            if path.exists() and json.loads(path.read_text()).get('state') in ('stopped','failed','interrupted'):
                self.store.execute("UPDATE live_overview_runs SET enabled=0,epoch=epoch+1,state='finished' WHERE recording_id=?",(rid,));return None
        if self.store.one("SELECT 1 FROM jobs WHERE state='running' OR (state='queued' AND COALESCE(kind,'transcription')!='retrieval_index') LIMIT 1"):raise Deferred('优先处理正式任务，概览稍后更新')
        parts,gaps=self.source(rid,config['engine'])
        if not parts:raise Deferred('等待已确认文字')
        if session.get('duration',0)-max(p['end'] for p in parts)>40:raise Deferred('转写存在积压，优先等待确认文字')
        if not forced and not self.latest(rid) and max(p['end'] for p in parts)<30 and sum(len(p['text']) for p in parts)<300:raise Deferred('积累约30秒确认文字后生成首版')
        profile=self.store.one('SELECT * FROM llm_configs WHERE id=?',(config['profile_id'],))
        if not profile or any(profile[k]!=config['provider'][k] for k in ('id','provider_type','base_url','model')):raise ValueError('模型配置已变化，请重新核对后开启概览')
        if profile['provider_type']=='cloud' and not config.get('cloud_confirmed'):raise ValueError('缺少本次云端概览确认')
        meta=self.meta(rid,session);meta_hash=digest(meta);snapshot=self.latest(rid)
        cursor=snapshot['cursor'] if snapshot else 0
        if cursor>len(parts) or (snapshot and digest(parts[:cursor])!=snapshot['covered_hash']):cursor=0
        if not forced and cursor==len(parts) and snapshot and snapshot['meta_hash']==meta_hash:return None
        sources={p['id']:p for p in parts};note_hashes={n['id']:digest(n) for n in meta['focus_notes']}
        existing=[i for i in self.effective(rid,snapshot) if evidence_valid(i,sources) and not any(note_hashes.get(k)!=v for k,v in i.get('note_fingerprints',{}).items())]
        current=[item_core(i) for i in existing if i.get('decision')!='dismissed']
        dismissed=[item_core(i) for i in existing if i.get('decision')=='dismissed'][-20:]
        window=profile.get('context_window',32768)
        budget=min(18000,max(1000,int((window-4096-1500)*.55)))
        evidence_ids={e['segment_id'] for i in current+dismissed for e in i['evidence']}
        context=[p for p in parts if p['id'] in evidence_ids]
        context+=parts[max(0,cursor-2):cursor]
        # A human focus is an instruction to inspect nearby sources, never proof of a fact.
        for note in meta['focus_notes']:
            context.extend(p for p in parts if p['start']<=note['time']+30 and p['end']>=note['time']-30)
        context=list({p['id']:p for p in context}.values())
        snapshot_payload=json.loads(snapshot['payload']) if snapshot else {}
        agenda=snapshot_payload.get('agenda',[])
        agenda_topics=[item for item in agenda if item['kind']=='topic' and evidence_valid(item,sources)]
        data={'agenda_topics':[{'id':t['id'],'content':t['content']} for t in agenda_topics],'current_items':current,'dismissed_items':dismissed,**meta,'context':context,'new_segments':[],'missing_range_count':len(gaps),'unrecognized_ranges':[{'start':a,'end':b} for a,b in gaps[-20:]]}
        if len(encode(data))>budget:raise ValueError('概览上下文超过会中预算，请减少选入简记或缩短已确认条目后继续')
        end=cursor
        for part in parts[cursor:]:
            candidate={**data,'new_segments':data['new_segments']+[part]}
            if len(encode(candidate))>budget:break
            data=candidate;end+=1
            if sum(len(s['text']) for s in data['new_segments'])>=8000:break
        if end==cursor and cursor<len(parts):raise ValueError('单段文字超过概览预算，请切换更大上下文模型')
        return {'rid':rid,'run':run,'profile':profile,'config':config,'input':data,'parts':parts,'cursor':end,
            'meta_hash':meta_hash,'covered_hash':digest(parts[:end]),'existing':existing,'agenda':agenda,'agenda_topics':agenda_topics,'previous_terms':snapshot_payload.get('terms',[]),'snapshot_version':snapshot['version'] if snapshot else 0}

    def generate(self,plan):
        current=self.store.one('SELECT enabled,epoch FROM live_overview_runs WHERE recording_id=?',(plan['rid'],))
        if self.closed.is_set() or not current or not current['enabled'] or current['epoch']!=plan['run']['epoch']:raise Deferred('概览已暂停')
        provider=OpenAICompatibleProvider(plan['profile'])
        model_input,quotes=numbered_input(plan['input'])
        item_ids={short['id']:original['id'] for short,original in zip(model_input['current_items'],plan['input']['current_items'])}
        topic_ids={f't{n+1}':topic['id'] for n,topic in enumerate(plan.get('agenda_topics',[]))}
        note_ids={short['id']:original['id'] for short,original in zip(model_input['focus_notes'],plan['input']['focus_notes'])}
        payload={'model':plan['config']['model'],'temperature':0,'max_tokens':3072,'stream':False,
            'messages':[{'role':'system','content':INSTRUCTION},{'role':'user','content':encode(model_input)}],
            'response_format':{'type':'json_schema','json_schema':{'name':'meeting_overview','schema':ModelOutput.model_json_schema()}}}
        with tempfile.TemporaryDirectory(prefix='overview-',dir=self.store.root) as work:
            request=Path(work)/'request.json';output=Path(work)/'output.json';request.write_text(encode(payload))
            env=__import__('os').environ.copy();env['MEETING_LLM_API_KEY']=provider.token
            if plan['profile']['provider_type']=='local' and 'qwen' in plan['config']['model'].lower():env['MEETING_LMSTUDIO_REASONING_OFF']='1'
            with (Path(work)/'worker.log').open('wb') as log:
                process=subprocess.Popen([sys.executable,str(BASE/'scripts/lmstudio_analyze.py'),'--base-url',provider.base_url,'--request',str(request),'--output',str(output)],stdout=log,stderr=log,env=env)
                started=time.monotonic()
                try:
                    while process.poll() is None:
                        run=self.store.one('SELECT enabled,epoch FROM live_overview_runs WHERE recording_id=?',(plan['rid'],))
                        if self.closed.is_set() or not run or not run['enabled'] or run['epoch']!=plan['run']['epoch']:raise Deferred('概览已暂停')
                        session=self.session(plan['rid'])
                        if session['state']=='completed' or self.store.one('SELECT 1 FROM meetings WHERE id=?',(plan['rid'],)):raise Deferred('录音已结束，已保留上一版')
                        if self.store.one("SELECT 1 FROM jobs WHERE state='running' OR (state='queued' AND COALESCE(kind,'transcription')!='retrieval_index') LIMIT 1"):raise Deferred('优先处理正式任务')
                        if time.monotonic()-started>generation_timeout(plan['profile']):raise ValueError(f"概览生成超过 {generation_timeout(plan['profile'])} 秒，已保留原文与上一版；请重试或选择更快的模型")
                        self.closed.wait(.2)
                    if process.returncode:
                        log.flush()
                        raise ValueError(worker_failure((Path(work)/'worker.log').read_text(errors='replace'))+'；原文与上一版均已保留')
                    return expand_numbered(json.loads(output.read_text()),quotes,item_ids,note_ids,topic_ids)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=2)
                        except subprocess.TimeoutExpired:process.kill();process.wait()

    def publish(self,plan,result):
        result=OverviewOutput.model_validate(result)
        if not result.items and plan['input']['current_items']:raise ValueError('模型返回空概览，已保留上一版')
        allowed={p['id']:p for p in plan['input']['context']+plan['input']['new_segments']}
        old={i['id']:i for i in plan['existing']};notes={n['id'] for n in plan['input']['focus_notes']}
        known_topics={item['id'] for item in plan.get('agenda_topics',[])}
        new_topics={item.topic_ref:uid() for item in result.items
                    if item.kind=='topic' and item.topic_ref and re.fullmatch(r'new_[1-9]',item.topic_ref)}
        fresh={part['id'] for part in plan['input']['new_segments']}
        fresh_topics=[item for item in result.items if item.kind=='topic'
                      and any(entry.segment_id in fresh for entry in item.evidence)]
        matched=matching_agenda_topic(plan) if len(fresh_topics)<=1 else None
        values=[];seen=set();early_duplicates=0
        matched_topic=next((topic for topic in plan.get('agenda_topics',[]) if topic['id']==matched),None)
        ordered=sorted(result.items,key=lambda item: not any(entry.segment_id in fresh for entry in item.evidence)) if matched else result.items
        for item in ordered:
            if not item.content.strip():raise ValueError('概览内容为空')
            value=item.model_dump();key=value['id']
            ref=item.topic_ref
            resolved=ref if ref in known_topics else new_topics.get(ref)
            is_fresh=any(entry.segment_id in fresh for entry in item.evidence)
            prior=old.get(key)
            if matched and is_fresh:
                if item.kind!='topic' and any(
                    existing['kind']==item.kind and existing.get('topic_ref') not in (None,matched)
                    and existing['content']==item.content for existing in plan.get('agenda',[])
                ):
                    continue
                resolved=matched
                if item.kind=='topic':
                    value['content']=matched_topic['content']
            if item.kind=='topic' and resolved:
                key=resolved
            if key and key not in old and key not in known_topics and key not in new_topics.values():raise ValueError('概览返回未知的条目ID')
            if not evidence_valid(value,allowed):raise ValueError('概览引用不在本轮已确认文字中')
            if not set(item.note_ids)<=notes:raise ValueError('概览返回不存在或未选入的简记关联')
            prior=old.get(key)
            if item.kind!='topic' and prior and prior.get('topic_ref'):
                same_topic=not resolved or resolved==prior['topic_ref']
                same_source=bool({e['segment_id'] for e in prior['evidence']}
                                 & {e['segment_id'] for e in value['evidence']})
                if not same_topic or not same_source:
                    key=None
            if key is None:
                match=next((i for i in old.values() if same_item(i,value)),None)
                key=match['id'] if match else uid()
            if key in seen:
                if matched and item.kind=='topic':continue
                if any(same_item(value,prior) for prior in values):early_duplicates+=1;continue
                raise ValueError('概览重复返回同一条目')
            seen.add(key);value['id']=key
            value['topic_ref']=None if item.kind=='topic' else resolved or (old.get(key) or {}).get('topic_ref')
            if not value['topic_ref']:value.pop('topic_ref')
            value['evidence']=[{**e,'start':allowed[e['segment_id']]['start'],'range_id':allowed[e['segment_id']]['range_id'],
                'segment_index':allowed[e['segment_id']]['segment_index']} for e in value['evidence']]
            value['note_fingerprints']={n['id']:digest(n) for n in plan['input']['focus_notes'] if n['id'] in value['note_ids']}
            values.append(value)
        if matched_topic and not fresh_topics:
            evidence=next((entry for value in values if value.get('topic_ref')==matched
                           for entry in value['evidence'] if entry['segment_id'] in fresh),None)
            if evidence:
                values=[value for value in values if value['id']!=matched]
                values.insert(0,{**matched_topic,'evidence':[evidence]})
        with self.recordings.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run=db.execute('SELECT * FROM live_overview_runs WHERE recording_id=?',(plan['rid'],)).fetchone()
            if not run or not run['enabled'] or run['epoch']!=plan['run']['epoch'] or self.session(plan['rid'])['state']=='completed' or db.execute('SELECT 1 FROM meetings WHERE id=?',(plan['rid'],)).fetchone():raise Deferred('录音已结束或概览已暂停，未发布迟到结果')
            session=self.session(plan['rid'])
            capture=self.recordings.folder(plan['rid'])/'capture.json'
            if session.get('source')=='macos' and capture.exists() and json.loads(capture.read_text()).get('state') in ('stopped','failed','interrupted'):raise Deferred('录音已结束，未发布迟到结果')
            current,_=self.source(plan['rid'],plan['config']['engine'])
            current_map={p['id']:p for p in current}
            if any(current_map.get(p['id'])!=p for p in plan['input']['context']+plan['input']['new_segments']):raise Deferred('本轮引用上下文已变化，等待重新整理')
            if digest(current[:plan['cursor']])!=plan['covered_hash'] or digest(self.meta(plan['rid'],self.session(plan['rid'])))!=plan['meta_hash']:raise Deferred('原文或关注点已变化，等待重新整理')
            profile=db.execute('SELECT * FROM llm_configs WHERE id=?',(plan['config']['profile_id'],)).fetchone()
            if not profile or any(plan['profile'][k]!=profile[k] for k in ('base_url','provider_type','model')):raise Deferred('模型配置已变化，结果未发布')
            protected={r['item_id']:dict(json.loads(r['payload']),decision=r['state']) for r in db.execute("SELECT * FROM live_overview_reviews WHERE recording_id=? AND state!='pending'",(plan['rid'],))}
            values=[i for i in values if i['id'] not in protected and not any(p['kind']==i['kind'] and p['content']==i['content'] for p in protected.values())]
            new_ids={i['id'] for i in values};new_content={(i['kind'],i['content']) for i in values}
            topics=any(i['kind']=='topic' for i in values)
            carried=[i for i in plan['existing'] if i['id'] not in new_ids and i['id'] not in protected and
                i.get('decision')!='dismissed' and (i['kind'],i['content']) not in new_content and not (topics and i['kind']=='topic')]
            pinned=[i for i in protected.values() if i['decision']=='confirmed']
            agenda=update_agenda(plan.get('agenda',[]),values+pinned)
            values,quality=select_items(values,carried,pinned,MAX_ITEMS)
            terms={(t['category'],t['original'].casefold()):t for t in plan.get('previous_terms',[])}
            for term in result.terms:
                evidence=term['evidence']
                if evidence_valid({'evidence':[evidence]},allowed) and term['original'] in evidence['quote']:
                    terms[(term['category'],term['original'].casefold())]=term
            terms=list(terms.values())[-32:]
            quality['duplicates_removed']+=early_duplicates
            version=(db.execute('SELECT MAX(version) FROM live_overview_snapshots WHERE recording_id=?',(plan['rid'],)).fetchone()[0] or 0)+1
            db.execute('INSERT INTO live_overview_snapshots(recording_id,version,cursor,covered_hash,meta_hash,payload) VALUES(?,?,?,?,?,?)',
                (plan['rid'],version,plan['cursor'],plan['covered_hash'],plan['meta_hash'],encode({'items':values,'agenda':agenda,'terms':terms,'covered_until':max((p['end'] for p in current[:plan['cursor']]),default=0),'filtered_count':result.filtered_count,'quality':quality,'prompt_version':PROMPT_VERSION,'provider':{**plan['config']['provider'],'name':plan['config']['name'],'engine':plan['config']['engine']}})))
            retained=[row['version'] for row in topic_snapshots(db,plan['rid'])]
            if retained:
                placeholders=','.join('?' for _ in retained)
                db.execute(f"DELETE FROM live_overview_snapshots WHERE recording_id=? AND version<=? "
                           f"AND version NOT IN ({placeholders})",(plan['rid'],version-12,*retained))
            else:
                db.execute('DELETE FROM live_overview_snapshots WHERE recording_id=? AND version<=?',(plan['rid'],version-12))
            db.execute("UPDATE live_overview_runs SET state='waiting',next_due=?,force=0,failures=0,error='',updated=CURRENT_TIMESTAMP WHERE recording_id=?",(time.time()+INTERVAL,plan['rid']))

    def run_once(self,rid,force=False):
        if not self.lock.acquire(blocking=False):return
        try:
            run=self.store.one('SELECT * FROM live_overview_runs WHERE recording_id=?',(rid,))
            if not run or not run['enabled'] or (not force and run['next_due']>time.time()):return
            try:
                plan=self.prepare(rid,run,force or bool(run['force']))
                if plan is None:
                    self.store.execute("UPDATE live_overview_runs SET error='',next_due=? WHERE recording_id=? AND enabled=1 AND epoch=?",(time.time()+3,rid,run['epoch']))
                    return
                self.store.execute("UPDATE live_overview_runs SET state='running',last_attempt=?,force=0,error='' WHERE recording_id=? AND epoch=?",(time.time(),rid,run['epoch']))
                if self.resources:self.resources.clear_observed(('overview',rid))
                resource='cloud_llm' if plan['profile']['provider_type']=='cloud' else 'local_llm'
                try:
                    with optional_slot(self.resources,'live_overview',resource,rid):
                        result=(self.generator or self.generate)(plan)
                except ResourceBusy as error:
                    if self.resources:self.resources.observe_wait(('overview',rid),'live_overview',{resource:1},ttl=5)
                    raise Deferred(str(error)) from None
                self.publish(plan,result)
            except Deferred as error:
                self.store.execute("UPDATE live_overview_runs SET state='waiting',error=?,next_due=? WHERE recording_id=? AND enabled=1 AND epoch=?",(str(error),time.time()+3,rid,run['epoch']))
            except Exception as error:
                self.store.execute("UPDATE live_overview_runs SET failures=failures+1,state='failed',error=?,next_due=?,enabled=CASE WHEN failures>=2 THEN 0 ELSE enabled END WHERE recording_id=? AND epoch=?",(('概览格式不完整，已保留原文和上一版' if isinstance(error,(ValidationError,json.JSONDecodeError)) else str(error)[:500]),time.time()+60,rid,run['epoch']))
        finally:self.lock.release()

    def start_monitor(self):
        self.store.execute("UPDATE live_overview_runs SET state='waiting' WHERE enabled=1 AND state='running'")
        def loop():
            while not self.closed.wait(1):
                for row in self.store.all('SELECT recording_id FROM live_overview_runs WHERE enabled=1 AND next_due<=? ORDER BY last_attempt LIMIT 8',(time.time(),)):
                    if self.closed.is_set():break
                    self.run_once(row['recording_id'])
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(timeout=4)


def register_overview(app,service):
    def invoke(fn,*args):
        try:return fn(*args)
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        except ValueError as error:raise HTTPException(409,str(error))
    @app.get('/api/recordings/{rid}/overview')
    def read(rid:str,version:int|None=None):return invoke(service.read,rid,version)
    @app.put('/api/recordings/{rid}/overview')
    def enable(rid:str,body:EnableInput):return invoke(service.enable,rid,body)
    @app.post('/api/recordings/{rid}/overview/pause')
    def pause(rid:str):return invoke(service.disable,rid)
    @app.post('/api/recordings/{rid}/overview/refresh')
    def refresh(rid:str):return invoke(service.refresh,rid)
    @app.put('/api/recordings/{rid}/overview/items/{item_id}')
    def review(rid:str,item_id:str,body:ReviewInput):return invoke(service.decide,rid,item_id,body)
