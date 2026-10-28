"""Explicit, immutable delivery records; drafts and live task state remain separate."""
import hashlib,json
from . import meeting_flow,minutes_versions
from .db import encode
from .minutes import MeetingMinutes,validate_minutes_citations


def create(store,mid,aid,include_citations,confirmed):
    if not confirmed:raise ValueError('请核对当前采用稿后确认交付')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
        artifact=minutes_versions.selected(db,mid,row['revision']) if row else None
        if not artifact or artifact['id']!=aid or not meeting_flow.artifact_current(db,mid,json.loads(artifact['provenance'])):
            raise ValueError('采用稿或来源已变化，请重新核对后交付')
        document=MeetingMinutes.model_validate(json.loads(artifact['payload']))
        segments=json.loads(row['segments']);validate_minutes_citations(document,{s['id'] for s in segments})
        if not any(document.model_dump().get(k) for k in ('overview','conclusions','decisions','action_items','open_questions','summary')):
            raise ValueError('请先补充纪要正文')
        bindings=meeting_flow.action_bindings(db,mid,artifact)
        linked={t['id']:t for t in meeting_flow.tasks(db,mid)}
        # This is an as-of execution record, never substituted for the meeting's original commitments.
        status=[{'action_index':int(index),'task_id':tid,'content':linked[tid]['content'],
                 'status':linked[tid]['status'],'owner':linked[tid]['owner'],'due_date':linked[tid]['due_date']}
                for index,tid in bindings.items() if tid in linked]
        payload={'document':document.model_dump(),'segments':segments,'revision':row['revision'],
                 'include_citations':include_citations,'task_status':status}
        key=hashlib.sha256(encode([mid,aid,payload]).encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO minutes_deliveries(id,meeting_id,artifact_id,payload) VALUES(?,?,?,?)',(key,mid,aid,encode(payload)))
        return public(db.execute('SELECT * FROM minutes_deliveries WHERE id=?',(key,)).fetchone())


def public(row):
    payload=json.loads(row['payload'])
    return {'id':row['id'],'artifact_id':row['artifact_id'],'created':row['created'],
            'title':payload['document']['title'],'revision':payload['revision'],
            'include_citations':payload['include_citations'],'task_status':payload['task_status']}
