"""Meeting preparation, human attention decisions, and explicit outcome provenance."""
from . import minutes_versions
import hashlib,json
from .db import encode
from .notes import fingerprint
from .note_expansion import nearby_segments

EMPTY_BRIEF={'title':'','purpose':'','agenda':'','participants':'','questions':'','previous_meeting_id':None}

def brief(db,mid):
    row=db.execute('SELECT payload FROM meeting_briefs WHERE meeting_id=?',(mid,)).fetchone()
    return {**EMPTY_BRIEF,**json.loads(row['payload'])} if row else dict(EMPTY_BRIEF)

def save_brief(db,mid,value):
    value={**EMPTY_BRIEF,**value}
    for key in ('interview','source_language','output_language'):
        if not value.get(key):value.pop(key,None)
    previous=value.get('previous_meeting_id')
    if previous and (previous==mid or not db.execute('SELECT id FROM meetings WHERE id=?',(previous,)).fetchone()):raise ValueError('关联会议不存在，或不能关联自己')
    db.execute('INSERT INTO meeting_briefs(meeting_id,payload) VALUES(?,?) ON CONFLICT(meeting_id) DO UPDATE SET payload=excluded.payload,updated=CURRENT_TIMESTAMP',(mid,encode(value)))

def dispositions(db,mid,revision):
    notes={n['id']:dict(n) for n in db.execute('SELECT * FROM meeting_notes WHERE meeting_id=?',(mid,))}
    result={}
    for row in db.execute('SELECT * FROM attention_reviews WHERE meeting_id=?',(mid,)):
        item=dict(row);item['source_segment_ids']=json.loads(item['source_segment_ids'])
        note=notes.get(item['note_id'])
        item['stale']=not note or fingerprint(note)!=item['note_fingerprint'] or (item['decision']=='meeting' and item['revision']!=revision)
        result[item['note_id']]=item
    return result

def attention_counts(db,mid,revision):
    reviews=dispositions(db,mid,revision);counts={'pending_meeting':0,'optional':0,'confirmed_meeting':0}
    for note in db.execute('SELECT id,note_scope FROM meeting_notes WHERE meeting_id=?',(mid,)):
        item=reviews.get(note['id']);decision=item['decision'] if item else None
        if decision in ('personal','deferred'):counts['optional']+=1
        elif (not item or item['stale']) and (note['note_scope']=='meeting' or decision=='meeting'):counts['pending_meeting']+=1
        elif item and not item['stale'] and decision=='meeting':counts['confirmed_meeting']+=1
        else:counts['optional']+=1
    return counts


def review_attention(store,mid,nid,body):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone();note=db.execute('SELECT * FROM meeting_notes WHERE meeting_id=? AND id=?',(mid,nid)).fetchone()
        if not row or not note:raise ValueError('会议或关注点不存在')
        if body.fingerprint!=fingerprint(note) or body.revision!=row['revision']:raise ValueError('记录或文字稿已变化，请重新核对')
        if not body.confirmed:raise ValueError('请先确认本次处理选择')
        ids=list(dict.fromkeys(body.source_segment_ids));content=body.content.strip()
        if body.decision=='meeting':
            nearby={s['id'] for s in nearby_segments(json.loads(row['segments']),note['captured_at'])}
            if not content or not row['revision'] or not ids or not set(ids)<=nearby:raise ValueError('纳入会议成果需要完整表述及当前附近原文依据')
        else:ids=[];content=''
        # A personal disposition must also remove the raw note from default retrieval.
        if body.decision=='personal' and note['note_scope']!='personal':
            db.execute("UPDATE meeting_notes SET note_scope='personal',expanded_text='',accepted_text='',source_segment_ids='[]',status='raw',expansion_revision=0,updated=CURRENT_TIMESTAMP WHERE id=?",(nid,))
            note=db.execute('SELECT * FROM meeting_notes WHERE id=?',(nid,)).fetchone()
        fp=fingerprint(note);key=hashlib.sha256(encode([nid,fp,body.decision,content,ids,row['revision'] if ids else None]).encode()).hexdigest()
        db.execute('''INSERT INTO attention_reviews(note_id,meeting_id,decision,content,source_segment_ids,revision,note_fingerprint,review_key)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET decision=excluded.decision,content=excluded.content,
            source_segment_ids=excluded.source_segment_ids,revision=excluded.revision,note_fingerprint=excluded.note_fingerprint,review_key=excluded.review_key,updated=CURRENT_TIMESTAMP''',
            (nid,mid,body.decision,content,encode(ids),row['revision'],fp,key))

def ai_context(db,mid):
    row=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
    if not row:raise ValueError('会议不存在')
    approved=[{'note_id':n['note_id'],'content':n['content'],'source_segment_ids':n['source_segment_ids'],'review_key':n['review_key']}
              for n in dispositions(db,mid,row['revision']).values() if n['decision']=='meeting' and not n['stale']]
    approved.sort(key=lambda n:n['note_id'])
    # Never silently omit approved priorities; require the user to reduce an oversized set.
    if len(approved)>40 or sum(len(n['content']) for n in approved)>12000:raise ValueError('已确认关注点超过本次整理预算，请减少后再生成纪要')
    preparation=brief(db,mid)
    # Generation preferences have their own task/checkpoint identity.
    for key in ('source_language','output_language'):
        preparation.pop(key,None)
    payload={'preparation':preparation,'reviewed_attention':approved}
    from .terminology import confirmed_context
    terms=confirmed_context(db,mid)
    if terms:payload['confirmed_terminology']=terms
    from .live_overview import approved_context
    segments=json.loads(db.execute('SELECT segments FROM meetings WHERE id=?',(mid,)).fetchone()['segments'])
    overview=approved_context(db,mid,segments)
    if overview:payload['reviewed_live_overview']=overview
    return payload,hashlib.sha256(encode(payload).encode()).hexdigest()

def artifact_reasons(db,mid,provenance,revision=None,_seen=None):
    """Shared provenance validity, with stable reason codes instead of private details."""
    try:
        row=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
        if not row:return ['meeting_missing']
        current=row['revision'];reasons=[];seen=set(_seen or ())
        if revision is not None and revision!=current:reasons.append('transcript_changed')
        if not isinstance(provenance,dict):return ['invalid_provenance']
        for aid in provenance.get('source_minutes_dependencies',[]):
            if aid in seen:
                reasons.append('dependency_cycle');continue
            if len(seen)>=100:
                reasons.append('dependency_limit');continue
            parent=db.execute("SELECT revision,provenance FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(aid,mid)).fetchone()
            if not parent:reasons.append('dependency_missing')
            elif artifact_reasons(db,mid,json.loads(parent['provenance']),parent['revision'],seen|{aid}):reasons.append('dependency_stale')
        if provenance.get('flow_context_hash') and ai_context(db,mid)[1]!=provenance['flow_context_hash']:reasons.append('meeting_context_changed')
        reviews=dispositions(db,mid,current)
        for item in provenance.get('adopted_notes',[]):
            note=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(item.get('note_id'),mid)).fetchone()
            if not note or (item.get('note_fingerprint') and fingerprint(note)!=item['note_fingerprint']):reasons.append('note_changed')
            key=item.get('attention_review_key');review=reviews.get(item.get('note_id'))
            if key and (not review or review['stale'] or review['review_key']!=key or review['decision']!='meeting'):reasons.append('attention_changed')
        return list(dict.fromkeys(reasons))
    except (ValueError,TypeError,KeyError,AttributeError):return ['invalid_provenance']


def artifact_current(db,mid,provenance,_seen=None):
    return not artifact_reasons(db,mid,provenance,_seen=_seen)

def todo_stale(db,todo):
    meeting=db.execute('SELECT revision FROM meetings WHERE id=?',(todo['meeting_id'],)).fetchone()
    if not meeting:return True
    if todo.get('source_kind')=='minutes':
        source=db.execute("SELECT revision,provenance FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(todo['source_artifact_id'],todo['meeting_id'])).fetchone()
        return not source or source['revision']!=meeting['revision'] or not artifact_current(db,todo['meeting_id'],json.loads(source['provenance']))
    note=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(todo['note_id'],todo['meeting_id'])).fetchone()
    if not note or fingerprint(note)!=todo['note_fingerprint']:return True
    ids=todo['source_segment_ids'];ids=json.loads(ids) if isinstance(ids,str) else ids
    if ids and todo['revision']!=meeting['revision']:return True
    if todo.get('attention_review_key'):
        item=dispositions(db,todo['meeting_id'],meeting['revision']).get(todo['note_id'])
        if not item or item['stale'] or item['decision']!='meeting' or item['review_key']!=todo['attention_review_key']:return True
    return False

def flow(store,mid):
    with store.connect() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
        if not row:raise ValueError('会议不存在')
        reviews=dispositions(db,mid,row['revision']);notes=db.execute('SELECT id,note_type FROM meeting_notes WHERE meeting_id=? ORDER BY captured_at,created,rowid',(mid,)).fetchall()
        artifact=minutes_versions.selected(db,mid,row['revision'])
        doc=json.loads(artifact['payload']) if artifact else {};provenance=json.loads(artifact['provenance']) if artifact else {}
        valid_artifact=bool(artifact and artifact_current(db,mid,provenance))
        cited={sid for name in ('overview','conclusions','decisions','action_items','open_questions','summary') for item in doc.get(name,[]) for sid in item.get('source_segment_ids',[])} if valid_artifact else set()
        entries=[];counts={'pending':0,'meeting':0,'personal':0,'deferred':0,'stale':0,'adopted':0,'related':0}
        for note in notes:
            review=reviews.get(note['id']);state='stale' if review and review['stale'] else review['decision'] if review else 'pending';counts[state]+=1
            adopted=bool(valid_artifact and any(n.get('note_id')==note['id'] and (not n.get('attention_review_key') or (review and n['attention_review_key']==review['review_key'])) for n in provenance.get('adopted_notes',[])))
            related=bool(review and not review['stale'] and cited.intersection(review['source_segment_ids']))
            counts['adopted']+=int(adopted);counts['related']+=int(related)
            entries.append({'note_id':note['id'],'state':state,'adopted':adopted,'related':related,'review':review})
        value=brief(db,mid);previous=None
        if value['previous_meeting_id']:
            prev=db.execute('SELECT id,title FROM meetings WHERE id=?',(value['previous_meeting_id'],)).fetchone()
            if prev:
                previous=dict(prev);previous['todos']=[t for t in tasks(db,prev['id']) if t['status']=='pending' and t['note_scope']=='meeting']
                for todo in previous['todos']:todo['stale']=todo_stale(db,todo)
        return {'brief':value,'counts':counts,'attention':entries,'previous':previous,'has_current_minutes':valid_artifact}


def publish_preparation(db,mid,value):
    if not value:return
    value={**EMPTY_BRIEF,**value}
    for key in ('source_language','output_language'):
        if not value.get(key):value.pop(key,None)
    if value.get('previous_meeting_id') and not db.execute('SELECT id FROM meetings WHERE id=?',(value['previous_meeting_id'],)).fetchone():value['previous_meeting_id']=None
    save_brief(db,mid,value)
    if value.get('title','').strip():db.execute('UPDATE meetings SET title=? WHERE id=?',(value['title'].strip(),mid))
    previous=db.execute('SELECT payload FROM meeting_preparation WHERE id=1').fetchone()
    if previous:
        previous_value={k:v for k,v in json.loads(previous['payload']).items() if v is not None}
        current_value={k:v for k,v in value.items() if v is not None}
        if previous_value==current_value:
            db.execute('DELETE FROM meeting_preparation WHERE id=1')


def track_action(store,mid,aid,index,body):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
        source=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(aid,mid)).fetchone()
        latest=minutes_versions.selected(db,mid,row['revision'] if row else -1)
        if not row or not source or source['revision']!=row['revision'] or not latest or latest['id']!=aid or not artifact_current(db,mid,json.loads(source['provenance'])):raise ValueError('纪要或原文已变化，请重新核对')
        actions=json.loads(source['payload']).get('action_items',[])
        if not 0<=index<len(actions):raise ValueError('行动项不存在')
        if not body.confirmed or not body.content.strip():raise ValueError('请核对内容、负责人及期限后确认')
        action=actions[index];ids=action.get('source_segment_ids',[]);valid={s['id'] for s in json.loads(row['segments'])}
        if not ids or not set(ids)<=valid:raise ValueError('行动项缺少当前原文依据')
        bound=action_bindings(db,mid,source).get(str(index))
        if bound:return {'id':bound,'reused':True}
        text=body.content.strip();existing=db.execute("SELECT id,content,source_artifact_id,source_index FROM note_todos WHERE meeting_id=? AND note_scope='meeting'",(mid,)).fetchall()
        for todo in existing:
            if todo['content'].strip()==text or (todo['source_artifact_id']==aid and todo['source_index']==index):
                bind_action(db,mid,aid,index,todo['id']);return {'id':todo['id'],'reused':True}
        key=hashlib.sha256(encode([mid,aid,index]).encode()).hexdigest()
        db.execute("INSERT INTO note_todos(id,meeting_id,note_id,content,owner,due_date,revision,note_fingerprint,source_segment_ids,note_scope,source_kind,source_artifact_id,source_index) VALUES(?,?,?,?,?,?,?,?,?,?,'minutes',?,?)",(key,mid,'',text,body.owner.strip(),body.due_date.strip(),row['revision'],'',encode(ids),'meeting',aid,index))
        bind_action(db,mid,aid,index,key)
        return {'id':key,'reused':False}


def tasks(db,mid):
    rows=db.execute("""SELECT t.*,m.title AS source_meeting_title FROM note_todos t
        JOIN meetings m ON m.id=t.meeting_id WHERE t.meeting_id=? OR EXISTS
        (SELECT 1 FROM meeting_task_links l WHERE l.meeting_id=? AND l.task_id=t.id)
        ORDER BY t.created,t.rowid""",(mid,mid)).fetchall()
    return [{**dict(t),'carried':t['meeting_id']!=mid,'stale':todo_stale(db,dict(t))} for t in rows]


def bind_action(db,mid,aid,index,tid):
    db.execute('INSERT OR REPLACE INTO minutes_task_bindings(meeting_id,artifact_id,action_index,task_id) VALUES(?,?,?,?)',(mid,aid,index,tid))


def action_bindings(db,mid,artifact):
    """Carry stable task identity through exact unchanged items in explicit edit lineage.

    Independent AI generations never inherit a binding just because their wording matches.
    """
    target=json.loads(artifact['payload']).get('action_items',[]);result={};seen=set();current=artifact
    while current and current['id'] not in seen and len(seen)<100:
        seen.add(current['id']);actions=json.loads(current['payload']).get('action_items',[])
        rows=db.execute('SELECT action_index,task_id FROM minutes_task_bindings WHERE artifact_id=?',(current['id'],)).fetchall()
        legacy=db.execute("SELECT source_index AS action_index,id AS task_id FROM note_todos WHERE meeting_id=? AND source_artifact_id=?",(mid,current['id'])).fetchall()
        for binding in [*rows,*legacy]:
            index=binding['action_index']
            if not 0<=index<len(actions):continue
            item=actions[index]
            # Ambiguous duplicate lines are not matched by content across versions.
            candidates=[i for i,a in enumerate(target) if a==item]
            if len(candidates)==1 and sum(a==item for a in actions)==1:
                result.setdefault(str(candidates[0]),binding['task_id'])
            elif current['id']==artifact['id'] and index<len(target):result.setdefault(str(index),binding['task_id'])
        parent=json.loads(current['provenance']).get('parent_minutes_id')
        current=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(parent,mid)).fetchone() if parent else None
    return result


def carry_task(store,mid,tid):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM meetings WHERE id=?',(mid,)).fetchone():raise ValueError('会议不存在')
        previous=brief(db,mid)['previous_meeting_id']
        todo=next((t for t in tasks(db,previous) if t['id']==tid),None) if previous else None
        if not todo or todo['note_scope']!='meeting':raise ValueError('只能带入关联上次会议的会议跟进项')
        if todo['meeting_id']==mid:raise ValueError('该事项已属于本次会议')
        if todo['status']!='pending' or todo['stale']:raise ValueError('事项已完成或来源变化，请先在来源会议核对')
        db.execute('INSERT OR IGNORE INTO meeting_task_links(meeting_id,task_id) VALUES(?,?)',(mid,tid))
        return {'id':tid}
