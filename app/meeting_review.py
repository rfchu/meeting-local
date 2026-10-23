"""Human review checkpoints bound to immutable input fingerprints."""
from . import minutes_versions
import hashlib
import json

STEPS=('transcript','speakers','notes','minutes')


class MeetingReview:
    def __init__(self,store):self.store=store

    @staticmethod
    def snapshot(db,mid):
        row=db.execute('SELECT title,revision,segments FROM meetings WHERE id=?',(mid,)).fetchone()
        if row is None:raise KeyError(mid)
        revision=row['revision'];segments=json.loads(row['segments'])
        notes=[dict(n) for n in db.execute('''SELECT id,raw_text,note_type,note_scope,is_marker,captured_at,
            expanded_text,accepted_text,status,source_segment_ids,expansion_revision FROM meeting_notes
            WHERE meeting_id=? ORDER BY id''',(mid,))]
        minutes=minutes_versions.selected(db,mid,revision)
        adopted=json.loads(minutes['provenance'] or '{}').get('adopted_notes',[]) if minutes else []
        by_id={n['id']:n for n in notes}
        adopted_inputs=[(item.get('note_id'),by_id.get(item.get('note_id'))) for item in adopted]
        records={r['step']:dict(r) for r in db.execute('SELECT * FROM meeting_reviews WHERE meeting_id=?',(mid,))}
        attention=[dict(n) for n in db.execute('SELECT * FROM attention_reviews WHERE meeting_id=? ORDER BY note_id',(mid,))]
        from .meeting_flow import brief
        inputs={'transcript':[revision,row['title'],segments],
                'speakers':[revision,[(s.get('id'),s.get('speaker'),s.get('speaker_locked')) for s in segments]],
                'notes':[revision,notes,attention], 'minutes':[revision,row['title'],{key:minutes[key] for key in ('id','payload','provenance')} if minutes else None,adopted_inputs,attention,brief(db,mid)]}
        from .meeting_flow import artifact_current
        minutes_invalid=bool(minutes and not artifact_current(db,mid,json.loads(minutes['provenance'])))
        steps={}
        for step in STEPS:
            fingerprint=hashlib.sha256(json.dumps(inputs[step],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            previous=records.get(step)
            if step=='notes' and not notes:state='not_needed'
            elif not revision or not segments or (step=='minutes' and (not minutes or minutes_invalid)):state='blocked'
            elif not previous:state='pending'
            elif previous['fingerprint']!=fingerprint:state='stale'
            else:state='done'
            steps[step]={'state':state,'fingerprint':fingerprint,'reviewed_at':previous['reviewed_at'] if previous else None,
                         'decision':previous['decision'] if previous else None}
        return {'revision':revision,'title':row['title'],'minutes_id':minutes['id'] if minutes else None,'steps':steps,'notes_count':len(notes),
                'speaker_count':len({s.get('speaker','') for s in segments}),
                'has_current_minutes':bool(minutes and not minutes_invalid)}

    def read(self,mid):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self.snapshot(db,mid)

    def set(self,mid,step,fingerprint,decision):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.snapshot(db,mid)['steps'][step]
            if current['fingerprint']!=fingerprint:raise ValueError('内容已经变化，请刷新后重新核对')
            if current['state'] in ('blocked','not_needed'):raise ValueError('当前尚无可核对的内容')
            if decision=='skipped' and step!='speakers':raise ValueError('仅发言人核对支持本次不区分')
            db.execute('''INSERT INTO meeting_reviews(meeting_id,step,fingerprint,decision) VALUES(?,?,?,?)
                ON CONFLICT(meeting_id,step) DO UPDATE SET fingerprint=excluded.fingerprint,
                decision=excluded.decision,reviewed_at=CURRENT_TIMESTAMP''',(mid,step,fingerprint,decision))
            return self.snapshot(db,mid)

    def reset(self,mid,step):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.snapshot(db,mid)
            db.execute('DELETE FROM meeting_reviews WHERE meeting_id=? AND step=?',(mid,step))
            return self.snapshot(db,mid)
