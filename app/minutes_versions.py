"""Separate the adopted working version from newly generated drafts."""
from .db import uid,encode

def selected(db,mid,revision=None):
    pin=db.execute('SELECT artifact_id FROM minutes_selection WHERE meeting_id=?',(mid,)).fetchone()
    if pin:
        row=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(pin['artifact_id'],mid)).fetchone()
        return row if row and (revision is None or row['revision']==revision) else None
    sql="SELECT * FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes'";args=[mid]
    if revision is not None:sql+=' AND revision=?';args.append(revision)
    return db.execute(sql+' ORDER BY created DESC,rowid DESC LIMIT 1',args).fetchone()

def adopt(db,mid,aid):
    db.execute('INSERT INTO minutes_selection(meeting_id,artifact_id) VALUES(?,?) ON CONFLICT(meeting_id) DO UPDATE SET artifact_id=excluded.artifact_id',(mid,aid))

def publish_draft(db,mid,revision,document,provenance):
    previous=selected(db,mid)
    if document.template_id=='online-interview' and previous:
        import json
        from .minutes import MeetingMinutes
        from .interview import preserve_review
        old=MeetingMinutes.model_validate(json.loads(previous['payload']))
        same=previous['revision']==revision and json.loads(previous['provenance']).get('flow_context_hash')==provenance.get('flow_context_hash')
        document=preserve_review(document,old,same)
    if previous:adopt(db,mid,previous['id'])
    aid=uid();db.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,'meeting_minutes',?,?)",(aid,mid,revision,encode(document.model_dump()),encode({**provenance,'draft':bool(previous)})))
    if not previous:adopt(db,mid,aid)
    return aid

def follow_edit(db,mid,old,new):
    pin=db.execute('SELECT artifact_id FROM minutes_selection WHERE meeting_id=?',(mid,)).fetchone()
    if pin and pin['artifact_id']==old:adopt(db,mid,new)
