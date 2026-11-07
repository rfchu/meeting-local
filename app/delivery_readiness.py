"""D3 shared delivery check and atomic explicit final confirmation.

Reads never write reviews. Source-processing receipts do not satisfy checkpoints.
"""
import hashlib,json
from .db import encode
from .meeting_review import MeetingReview
from . import meeting_flow,minutes_versions,minutes_delivery


def snapshot(db,mid,include_citations=False):
    row=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
    if not row:raise KeyError(mid)
    review=MeetingReview.snapshot(db,mid);chosen=minutes_versions.selected(db,mid)
    blockers=[];warnings=[]
    def issue(code,label,target,warning=False):
        (warnings if warning else blockers).append({'code':code,'label':label,'target':target})
    for step,label in (('transcript','文字稿与主题'),('speakers','发言人')):
        if review['steps'][step]['state']!='done':issue('review_'+step,'请先核对'+label,step)
    attention=meeting_flow.attention_counts(db,mid,row['revision'])
    if attention['pending_meeting']:issue('review_notes',f"{attention['pending_meeting']} 条会议关注点需确认用途与依据",'notes')
    payload=None
    if not chosen:issue('missing_minutes','请先生成并采用会议纪要','minutes')
    else:
        try:payload=minutes_delivery.prepare(db,mid,chosen['id'],include_citations)
        except ValueError as error:issue('invalid_minutes',str(error),'minutes')
    todos=meeting_flow.tasks(db,mid);active_stale=sum(t['note_scope']=='meeting' and t['status']=='pending' and t['stale'] for t in todos)
    candidates=0
    if payload:
        bindings=meeting_flow.action_bindings(db,mid,chosen)
        candidates=sum(str(i) not in bindings for i,_ in enumerate(payload['document'].get('action_items',[])))
        if candidates:issue('action_candidates',f'{candidates} 项行动候选尚未加入跟进，可按需处理','actions',True)
    if active_stale:issue('stale_tasks',f'{active_stale} 项跟进来源变化；请核对，历史记录保留','actions',True)
    current_ids=minutes_delivery.matching(db,mid,chosen['id'],payload,review=review) if chosen else []
    # Binding includes actual snapshot data and all checkpoint inputs/decisions, not UI labels alone.
    inputs=[chosen['id'] if chosen else None,payload,review,attention,[(t['id'],t['status'],t['stale']) for t in todos],blockers,warnings,include_citations]
    fingerprint=hashlib.sha256(encode(inputs).encode()).hexdigest()
    return {'version':1,'revision':row['revision'],'artifact_id':chosen['id'] if chosen else None,'include_citations':include_citations,
            'fingerprint':fingerprint,'can_finalize':not blockers,'blockers':blockers,'warnings':warnings,
            'minutes_review_state':review['steps']['minutes']['state'],'current_delivery_id':current_ids[0] if current_ids and not blockers and review['steps']['minutes']['state']=='done' else None,
            'historical_count':db.execute('SELECT count(*) FROM minutes_deliveries WHERE meeting_id=?',(mid,)).fetchone()[0],
            'counts':{**attention,'action_candidates':candidates,'active_stale_tasks':active_stale},'review':review}


def read(store,mid,include_citations=False):
    with store.connect() as db:
        db.execute('BEGIN');return snapshot(db,mid,include_citations)


def finalize(store,mid,body):
    if not body.confirmed:raise ValueError('请明确确认已核对当前采用稿及本次交付检查')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT * FROM minutes_deliveries WHERE meeting_id=? AND artifact_id=? AND json_extract(payload,'$.review_token')=? AND json_extract(payload,'$.include_citations')=? ORDER BY rowid DESC LIMIT 1",(mid,body.artifact_id,body.fingerprint,int(body.include_citations))).fetchone()
        # A response-lost retry returns its historical receipt; it never reapplies confirmation to new sources.
        if previous:return {**minutes_delivery.public(previous),'reused':True}
        value=snapshot(db,mid,body.include_citations)
        if value['artifact_id']!=body.artifact_id or value['fingerprint']!=body.fingerprint:raise ValueError('交付检查内容已变化，请刷新并重新确认')
        if not value['can_finalize']:raise ValueError(value['blockers'][0]['label'])
        if value['current_delivery_id'] and value['minutes_review_state']=='done':
            return {**minutes_delivery.public(db.execute('SELECT * FROM minutes_deliveries WHERE id=?',(value['current_delivery_id'],)).fetchone()),'reused':True}
        fp=value['review']['steps']['minutes']['fingerprint']
        db.execute("INSERT INTO meeting_reviews(meeting_id,step,fingerprint,decision) VALUES(?,'minutes',?,'confirmed') ON CONFLICT(meeting_id,step) DO UPDATE SET fingerprint=excluded.fingerprint,decision='confirmed',reviewed_at=CURRENT_TIMESTAMP",(mid,fp))
        payload=minutes_delivery.prepare(db,mid,body.artifact_id,body.include_citations)
        payload.update(task_sources=minutes_delivery.task_sources(db,mid),review_token=body.fingerprint,review_confirmation={step:value['review']['steps'][step]['fingerprint'] for step in ('transcript','speakers','minutes')})
        return {**minutes_delivery.persist(db,mid,body.artifact_id,payload),'reused':False}


def audience_projection(db, mid, body):
    payload = minutes_delivery.prepare(db, mid, body.artifact_id,
                                       body.include_citations if body.audience == 'internal' else False)
    if body.audience == 'external':
        return minutes_delivery.external_projection(db, mid, payload, body.selection)
    if body.selection:
        raise ValueError('内部版使用完整采用稿')
    return payload, []


def audience_snapshot(db, mid, body):
    payload, candidates = audience_projection(db, mid, body)
    checks = snapshot(db, mid, payload['include_citations'])
    payload.update(audience=body.audience, selection=sorted(set(body.selection)))
    from .job_checkpoints import digest
    fingerprint = digest([checks['fingerprint'], payload, candidates])
    return payload, candidates, checks, fingerprint


def audience_preview(store, mid, body):
    from .minutes import MeetingMinutes, minutes_markdown
    with store.connect() as db:
        db.execute('BEGIN')
        payload, candidates, checks, fingerprint = audience_snapshot(db, mid, body)
        return {
            'candidates': candidates,
            'markdown': minutes_markdown(MeetingMinutes.model_validate(payload['document']),
                                         payload['segments'], payload['include_citations'], citation_appendix=True),
            'fingerprint': fingerprint,
            'can_finalize': checks['can_finalize'] and (body.audience == 'internal' or bool(body.selection)),
        }


def finalize_audience(store, mid, body):
    if not body.confirmed:
        raise ValueError('请确认已核对本次预览')
    selection = sorted(set(body.selection))
    if body.audience == 'external' and not selection:
        raise ValueError('请至少选入一项对外内容')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute("""
            SELECT * FROM minutes_deliveries WHERE meeting_id=? AND artifact_id=?
            AND json_extract(payload,'$.review_token')=?
            AND json_extract(payload,'$.audience')=?
        """, (mid, body.artifact_id, body.fingerprint, body.audience)).fetchone()
        if previous:
            saved = json.loads(previous['payload'])
            if saved['selection'] == selection and saved['include_citations'] == (body.audience == 'internal' and body.include_citations):
                return {**minutes_delivery.public(previous), 'reused': True}
        payload, _, checks, fingerprint = audience_snapshot(db, mid, body)
        if body.fingerprint != fingerprint:
            raise ValueError('交付预览已变化，请重新预览后确认')
        if not checks['can_finalize']:
            raise ValueError(checks['blockers'][0]['label'])
        review = checks['review']
        confirmation = {step: review['steps'][step]['fingerprint'] for step in ('transcript','speakers','minutes')}
        payload.update(task_sources=minutes_delivery.task_sources(db,mid), review_confirmation=confirmation)
        existing = minutes_delivery.matching(db, mid, body.artifact_id, payload, review=review)
        for delivery_id in existing:
            saved = db.execute('SELECT * FROM minutes_deliveries WHERE id=?', (delivery_id,)).fetchone()
            if 'audience' in json.loads(saved['payload']):
                return {**minutes_delivery.public(saved), 'reused': True}
        db.execute("""
            INSERT INTO meeting_reviews(meeting_id,step,fingerprint,decision) VALUES(?,'minutes',?,'confirmed')
            ON CONFLICT(meeting_id,step) DO UPDATE SET fingerprint=excluded.fingerprint,
            decision='confirmed',reviewed_at=CURRENT_TIMESTAMP
        """, (mid, confirmation['minutes']))
        return {**minutes_delivery.persist(db,mid,body.artifact_id,{**payload,'review_token':body.fingerprint}),
                'reused':False}
