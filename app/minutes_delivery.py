"""Explicit, immutable delivery records; drafts and live task state remain separate."""
import hashlib,json
from . import meeting_flow,minutes_versions
from .db import encode
from .minutes import MeetingMinutes,validate_minutes_citations


def prepare(db,mid,aid,include_citations):
    """Prepare one immutable payload from the caller's transaction."""
    row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
    artifact=minutes_versions.selected(db,mid,row['revision']) if row else None
    if not artifact or artifact['id']!=aid or not meeting_flow.artifact_current(db,mid,json.loads(artifact['provenance'])):
        raise ValueError('采用稿或来源已变化，请重新核对后交付')
    document=MeetingMinutes.model_validate(json.loads(artifact['payload']))
    if document.interview_evaluation_needs_review:raise ValueError('请先复核来源变化后的面试官评价，再确认交付')
    segments=json.loads(row['segments']);validate_minutes_citations(document,{s['id'] for s in segments})
    if not any(document.model_dump().get(k) for k in ('overview','conclusions','decisions','action_items','open_questions','summary')):
        raise ValueError('请先补充纪要正文')
    bindings=meeting_flow.action_bindings(db,mid,artifact)
    linked={t['id']:t for t in meeting_flow.tasks(db,mid)}
    status=[{'action_index':int(index),'task_id':tid,'content':linked[tid]['content'],
             'status':linked[tid]['status'],'owner':linked[tid]['owner'],'due_date':linked[tid]['due_date']}
            for index,tid in bindings.items() if tid in linked]
    return {'document':document.model_dump(),'segments':segments,'revision':row['revision'],
            'include_citations':include_citations,'task_status':status}


def persist(db,mid,aid,payload):
    key=hashlib.sha256(encode([mid,aid,payload]).encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO minutes_deliveries(id,meeting_id,artifact_id,payload) VALUES(?,?,?,?)',(key,mid,aid,encode(payload)))
    return public(db.execute('SELECT * FROM minutes_deliveries WHERE id=?',(key,)).fetchone())


def task_sources(db,mid):
    return sorted([[t['id'],bool(t['stale'])] for t in meeting_flow.tasks(db,mid)])


def matching(db,mid,aid,payload,exact_citations=True,review=None):
    """Old snapshots remain readable; matching content is distinct from having sent it."""
    if not payload:return []
    keys=('document','segments','revision','task_status')+ (('include_citations',) if exact_citations else ())
    result=[];source_checks=None
    for row in db.execute('SELECT * FROM minutes_deliveries WHERE meeting_id=? AND artifact_id=? ORDER BY created DESC,rowid DESC',(mid,aid)):
        saved=json.loads(row['payload']);confirmation=saved.get('review_confirmation')
        if 'task_sources' in saved:
            if source_checks is None:source_checks=task_sources(db,mid)
            if saved['task_sources']!=source_checks:continue
        if confirmation and review and any(confirmation.get(step)!=review['steps'][step]['fingerprint'] or review['steps'][step]['state']!='done' for step in ('transcript','speakers','minutes')):continue
        if all(saved.get(key)==payload.get(key) for key in keys):result.append(row['id'])
    return result


def create(store,mid,aid,include_citations,confirmed):
    if not confirmed:raise ValueError('请核对当前采用稿后确认交付')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return persist(db,mid,aid,prepare(db,mid,aid,include_citations))


def public(row):
    payload=json.loads(row['payload'])
    return {'id':row['id'],'artifact_id':row['artifact_id'],'created':row['created'],
            'title':payload['document']['title'],'revision':payload['revision'],
            'include_citations':payload['include_citations'],'task_status':payload['task_status']}
