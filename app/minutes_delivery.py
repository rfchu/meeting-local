"""Explicit, immutable delivery records; drafts and live task state remain separate."""
import hashlib,json
from . import meeting_flow,minutes_versions
from .db import encode
from .minutes import MeetingMinutes,MinutesItem,validate_minutes_citations


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
        if saved.get('audience','internal') != payload.get('audience','internal'):continue
        if saved.get('selection',[]) != payload.get('selection',[]):continue
        if all(saved.get(key)==payload.get(key) for key in keys):result.append(row['id'])
    return result


def current_external(db, mid, aid, payload, review):
    if not payload:
        return None
    steps = ('transcript', 'speakers', 'minutes')
    if any(review['steps'][step]['state'] != 'done' for step in steps):
        return None
    confirmation = {step: review['steps'][step]['fingerprint'] for step in steps}
    for row in db.execute("""
        SELECT id,payload FROM minutes_deliveries WHERE meeting_id=? AND artifact_id=?
        AND json_extract(payload,'$.audience')='external' ORDER BY rowid DESC
    """, (mid, aid)):
        saved = json.loads(row['payload'])
        if saved['review_confirmation'] != confirmation or saved['task_sources'] != task_sources(db, mid):
            continue
        try:
            projected, _ = external_projection(db, mid, payload, saved['selection'])
        except ValueError:
            continue
        if all(saved.get(key) == value for key, value in projected.items()):
            return row['id']
    return None


def create(store,mid,aid,include_citations,confirmed):
    if not confirmed:raise ValueError('请核对当前采用稿后确认交付')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return persist(db,mid,aid,prepare(db,mid,aid,include_citations))


def public(row):
    payload=json.loads(row['payload'])
    return {'id':row['id'],'artifact_id':row['artifact_id'],'created':row['created'],
            'title':payload['document']['title'],'revision':payload['revision'],
            'audience':payload.get('audience','internal'),'sent_at':payload.get('sent_at'),
            'include_citations':payload['include_citations'],'task_status':payload['task_status']}


EXTERNAL_SECTIONS = {
    'overview': '会议概况', 'conclusions': '讨论要点',
    'decisions': '已定事项', 'action_items': '已确认行动',
}


def external_projection(db, mid, payload, selection):
    source = MeetingMinutes.model_validate(payload['document'])
    # Start from an empty document. Custom titles, metadata, templates, and
    # transcript excerpts can contain internal material even without citations.
    from .minutes_composition import semantic_template
    projected = MeetingMinutes(
        title='External Meeting Minutes' if source.output_language == 'en' else '对外会议纪要',
        output_language=source.output_language, style='formal',
        template_snapshot=semantic_template('general-meeting').model_dump(),
    )
    from unicodedata import normalize
    def normalized(text):
        return ''.join(normalize('NFKC', text).casefold().split())

    private_text = [source.interviewer_evaluation]
    for row in db.execute("SELECT raw_text,expanded_text,accepted_text FROM meeting_notes WHERE meeting_id=? AND note_scope='personal'", (mid,)):
        private_text.extend(row)
    if source.interview_context:
        private_text.extend(source.interview_context.model_dump().values())
    private = [normalized(value) for value in private_text if isinstance(value, str) and value.strip()]
    active_tasks = {task['id'] for task in meeting_flow.tasks(db, mid)
                    if not task['stale'] and task['note_scope'] == 'meeting'}
    confirmed_actions = {item['action_index']: item['content'].strip() for item in payload['task_status']
                         if item['task_id'] in active_tasks}
    candidates = []
    selected = set(selection)
    for section, label in EXTERNAL_SECTIONS.items():
        if source.interview and section == 'decisions':
            continue  # Interview decisions represent internal competency assessments.
        for index, item in enumerate(getattr(source, section)):
            content = normalized(item.content)
            if not item.source_segment_ids or any(secret in content for secret in private):
                continue
            if section == 'action_items' and confirmed_actions.get(index) != item.content.strip():
                continue
            key = f'{section}:{index}'
            candidates.append({'id': key, 'label': label, 'content': item.content})
            if key in selected:
                getattr(projected, section).append(MinutesItem(content=item.content))
    if selected - {item['id'] for item in candidates}:
        raise ValueError('有选入条目已变化或不可对外分享，请重新预览')
    return {
        'document': projected.model_dump(),
        'segments': [], 'revision': payload['revision'],
        'include_citations': False, 'task_status': [],
    }, candidates


def set_sent(store, mid, delivery_id, sent):
    """Record a manual sending status without changing the delivered content."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if sent:
            db.execute("""
                UPDATE minutes_deliveries SET payload=json_set(payload,'$.sent_at',CURRENT_TIMESTAMP)
                WHERE meeting_id=? AND id=? AND json_extract(payload,'$.sent_at') IS NULL
            """, (mid, delivery_id))
        else:
            db.execute("""
                UPDATE minutes_deliveries SET payload=json_remove(payload,'$.sent_at')
                WHERE meeting_id=? AND id=? AND json_type(payload,'$.sent_at') IS NOT NULL
            """, (mid, delivery_id))
        saved = db.execute('SELECT * FROM minutes_deliveries WHERE meeting_id=? AND id=?',
                           (mid, delivery_id)).fetchone()
        if not saved:
            raise KeyError(delivery_id)
        return public(saved)
