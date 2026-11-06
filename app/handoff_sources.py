"""Read-only concrete provenance inspection and immutable human processing receipts.

Receipts acknowledge inspection only; they never repair provenance or adopt output.
Stored as internal artifacts so existing backup/deletion transactions cover them.
"""
import hashlib,json
from .db import encode
from . import meeting_flow,minutes_versions
from .notes import fingerprint as note_fingerprint


def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()
def excerpt(value):return str(value or '')[:800]
def segment_view(segment):
    return {key:segment.get(key) for key in ('id','start','end','speaker','text')} if segment else None


def inspect(db,mid,artifact_id=None):
    meeting=db.execute('SELECT revision,segments FROM meetings WHERE id=?',(mid,)).fetchone()
    if not meeting:raise KeyError('会议不存在')
    artifact=(db.execute("SELECT * FROM artifacts WHERE meeting_id=? AND id=? AND kind='meeting_minutes'",(mid,artifact_id)).fetchone() if artifact_id else minutes_versions.selected(db,mid))
    if artifact_id and not artifact:raise KeyError('纪要不存在')
    options=[dict(row) for row in db.execute("SELECT id,revision,created FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 100",(mid,))]
    if artifact and not any(row['id']==artifact['id'] for row in options):options.insert(0,{key:artifact[key] for key in ('id','revision','created')})
    result={'revision':meeting['revision'],'artifact_id':artifact['id'] if artifact else None,'artifact_revision':artifact['revision'] if artifact else None,'artifacts':options,'items':[],'reason_codes':[]}
    if not artifact:return result
    aid=artifact['id'];raw_provenance=json.loads(artifact['provenance']);provenance=raw_provenance;document=json.loads(artifact['payload'])
    if not isinstance(provenance,dict):provenance={}
    current=json.loads(meeting['segments']);current_by={s['id']:s for s in current}
    old=db.execute('SELECT segments FROM revisions WHERE meeting_id=? AND revision=?',(mid,artifact['revision'])).fetchone()
    previous=json.loads(old['segments']) if old else (current if artifact['revision']==meeting['revision'] else [])
    old_by={s['id']:s for s in previous}
    reasons=meeting_flow.artifact_reasons(db,mid,raw_provenance,artifact['revision']);result['reason_codes']=reasons
    binding=[aid,artifact['revision'],provenance,meeting['revision']]
    def add(key,kind,label,status,before,now,target=None,detail=''):
        item={'source_id':key,'kind':kind,'label':label,'status':status,'before':excerpt(before),'current':excerpt(now),'target':target,'detail':detail}
        item['fingerprint']=digest([binding,item,before,now]);result['items'].append(item)
    add('transcript','revision','文字稿版本','changed' if artifact['revision']!=meeting['revision'] else 'current',f"v{artifact['revision']}",f"v{meeting['revision']}",detail='引用按片段 ID 对照，不推测改稿后的语义对应关系。')
    # Walk structured minutes, including interview templates, collecting explicit citations only.
    ids=[]
    def citations(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key=='source_segment_ids' and isinstance(child,list):ids.extend(s for s in child if isinstance(s,str))
                else:citations(child)
        elif isinstance(value,list):
            for child in value:citations(child)
    citations(document)
    for sid in dict.fromkeys(ids):
        before=old_by.get(sid);now=current_by.get(sid)
        status='missing' if not now else 'unavailable' if not before else 'current' if segment_view(before)==segment_view(now) else 'changed'
        add('segment:'+sid,'segment','原文引用 · '+sid,status,before.get('text') if before else '',now.get('text') if now else '',{'segment_id':sid,'start':now['start']} if now else None,'历史原文未保存，无法比较。' if not before else '同一片段 ID 的历史与当前原文；版本不同仍需核对。')
        result['items'][-1]['fingerprint']=digest([result['items'][-1]['fingerprint'],segment_view(before),segment_view(now)])
    dependency_cache={};dependency_budget=set()
    def dependency_signature(parent_id,seen=None):
        seen=set(seen or ())
        if parent_id in seen:return ['cycle',parent_id]
        if parent_id in dependency_cache:return dependency_cache[parent_id]
        if len(dependency_budget)>=100:return ['bounded',parent_id]
        dependency_budget.add(parent_id)
        parent=db.execute("SELECT revision,provenance FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(parent_id,mid)).fetchone()
        if not parent:return None
        prov=json.loads(parent['provenance'])
        if not isinstance(prov,dict):return ['invalid',prov]
        notes=[]
        for entry in prov.get('adopted_notes',[]):
            nid=entry.get('note_id');n=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)).fetchone()
            r=db.execute('SELECT review_key FROM attention_reviews WHERE note_id=? AND meeting_id=?',(nid,mid)).fetchone()
            notes.append([nid,note_fingerprint(n) if n else None,r['review_key'] if r else None])
        try:context=meeting_flow.ai_context(db,mid)[1] if prov.get('flow_context_hash') else None
        except ValueError:context=None
        value=digest([dict(parent),notes,context,[dependency_signature(p,seen|{parent_id}) for p in prov.get('source_minutes_dependencies',[])]])
        dependency_cache[parent_id]=value;return value
    for parent_id in dict.fromkeys(provenance.get('source_minutes_dependencies',[])):
        parent=db.execute("SELECT revision,provenance FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(parent_id,mid)).fetchone()
        codes=meeting_flow.artifact_reasons(db,mid,json.loads(parent['provenance']),parent['revision']) if parent else ['dependency_missing']
        add('parent:'+parent_id,'minutes','来源纪要','missing' if not parent else 'changed' if codes else 'current',parent_id,f"v{parent['revision']}" if parent else '',{'artifact_id':parent_id} if parent else None,'、'.join(codes) or '来源有效；可继续查看该稿引用。')
        result['items'][-1]['fingerprint']=digest([result['items'][-1]['fingerprint'],dependency_signature(parent_id)])
    reviews=meeting_flow.dispositions(db,mid,meeting['revision'])
    for group in ('adopted_notes','reviewed_attention'):
        for index,item in enumerate(provenance.get(group,[])):
            nid=item.get('note_id');note=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)).fetchone();review=reviews.get(nid)
            key=item.get('attention_review_key') or item.get('review_key')
            changed=not note or (item.get('note_fingerprint') and item['note_fingerprint']!=note_fingerprint(note)) or (key and (not review or review['stale'] or review['review_key']!=key or review['decision']!='meeting'))
            now=review['content'] if review and not review['stale'] and review['decision']=='meeting' else note['raw_text'] if note else ''
            add(f'{group}:{index}:{nid}','note','明确采用的简记' if group=='adopted_notes' else '整理时的已确认关注点','missing' if not note else 'changed' if changed else 'current',item.get('content',''),now,{'note_id':nid,'start':note['captured_at']} if note else None,'简记用于整理上下文，不代表其内容已自动采用。' if group=='reviewed_attention' else '核对内容与人工用途选择。')
            result['items'][-1]['fingerprint']=digest([result['items'][-1]['fingerprint'],note_fingerprint(note) if note else None,review])
    from .live_overview import approved_context
    try:approved={i['item_id']:i for i in approved_context(db,mid,current)}
    except ValueError:approved={}
    historical={i['item_id']:i for i in provenance.get('reviewed_live_overview',[])}
    overview_rows={row['item_id']:dict(row) for row in db.execute("SELECT * FROM live_overview_reviews WHERE recording_id=? AND state!='pending'",(mid,))}
    # Legacy outputs lack item snapshots: display present records as unverified history.
    overview_ids=list(historical) if 'reviewed_live_overview' in provenance else list(overview_rows) if provenance.get('flow_context_hash') else []
    for item_id in overview_ids:
        before=historical.get(item_id);now=approved.get(item_id);row=overview_rows.get(item_id)
        raw=json.loads(row['payload']) if row else None
        status='missing' if not row else 'unavailable' if before is None else 'current' if before==now else 'changed'
        add('overview:'+item_id,'overview','会中概览条目',status,before.get('content') if before else '',raw.get('content') if raw else '',{'item_id':item_id} if row else None,'仅作已确认整理关注点，不是独立事实依据。' if before else '旧纪要未保存概览条目快照；当前回看不能证明此条当时参与了整理。')
        result['items'][-1]['fingerprint']=digest([result['items'][-1]['fingerprint'],before,now,row])
    if provenance.get('flow_context_hash'):
        try:context_hash=meeting_flow.ai_context(db,mid)[1]
        except ValueError:context_hash=None
        add('context','context','会议资料与整理上下文','current' if context_hash==provenance['flow_context_hash'] else 'changed','已保存摘要校验值','当前资料与已确认上下文',detail='旧版本仅保存整体校验值，无法精确还原变更字段；请核对会议资料、术语与会中概览。')
        # Bind acknowledgment to the actual context, not its display label.
        result['items'][-1]['fingerprint']=digest([result['items'][-1]['fingerprint'],context_hash])
    receipts={}
    for row in db.execute("SELECT payload,created FROM artifacts WHERE meeting_id=? AND kind='handoff_review' ORDER BY rowid DESC",(mid,)):
        receipt=json.loads(row['payload'])
        if receipt['artifact_id']==aid:receipts.setdefault(receipt['source_id'],{**receipt,'created':row['created']})
    for item in result['items']:
        receipt=receipts.get(item['source_id']);item['feedback']=None
        if receipt:item['feedback']={'decision':receipt['decision'],'created':receipt['created'],'stale':receipt['fingerprint']!=item['fingerprint']}
    return result


def report(store,mid,artifact_id=None,offset=0,limit=25):
    with store.connect() as db:
        db.execute('BEGIN');value=inspect(db,mid,artifact_id)
        items=value['items'];value['total']=len(items);value['offset']=offset
        value['counts']={'changed':sum(i['status']!='current' for i in items),'processed':sum(bool(i['feedback'] and not i['feedback']['stale'] and i['feedback']['decision']!='reset') for i in items)}
        value['items']=items[offset:offset+limit];return value


def review(store,mid,body):
    if not body.confirmed:raise ValueError('请明确确认本次处理')
    command={key:getattr(body,key) for key in ('artifact_id','source_id','fingerprint','decision')}
    rid=digest([mid,body.request_id])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT payload FROM artifacts WHERE id=? AND meeting_id=? AND kind='handoff_review'",(rid,mid)).fetchone()
        if previous:
            saved=json.loads(previous['payload'])
            if any(saved.get(key)!=value for key,value in command.items()):raise ValueError('同一请求编号不能用于不同处理，请刷新后重试')
            return {'id':rid,'reused':True,'decision':saved['decision']}
        value=inspect(db,mid,body.artifact_id)
        item=next((i for i in value['items'] if i['source_id']==body.source_id),None)
        if not item or item['fingerprint']!=body.fingerprint:raise ValueError('来源已变化，请刷新后重新核对')
        db.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,'handoff_review',?,'{}')",(rid,mid,value['revision'],encode(command)))
        return {'id':rid,'reused':False,'decision':body.decision}
