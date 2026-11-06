"""Meeting lookup and presentation shared by feature routes."""
import json
from fastapi import HTTPException
from .minutes_templates import MinutesTemplate
from .recording_completion import recording_quality_summary
from . import minutes_versions
from . import meeting_flow, minutes_delivery

class MeetingQueries:
    def __init__(self,store,recordings,meeting_review,minutes_generation):
        self.store=store;self.recordings=recordings;self.meeting_review=meeting_review;self.minutes_generation=minutes_generation
    def meeting(self, mid):
        row = self.store.one('SELECT * FROM meetings WHERE id=?', (mid,))
        if not row:
            raise HTTPException(404, '会议不存在')
        return row
    def active(self, mid):
        return self.store.one("SELECT * FROM jobs WHERE meeting_id=? AND state IN ('running','queued')", (mid,))
    def minutes_template(self, template_id):
        row = self.store.one("SELECT definition FROM minutes_templates WHERE id=? AND status='active'", (template_id,))
        if not row:
            raise HTTPException(404, '纪要模板不存在或已停用')
        return MinutesTemplate.model_validate(json.loads(row['definition']))
    def decorated(self, row):
        row['segments'] = json.loads(row['segments'])
        row['live_overview_available'] = bool(self.store.one('SELECT 1 FROM live_overview_snapshots WHERE recording_id=? LIMIT 1', (row['id'],)))
        row['jobs'] = self.store.all("SELECT j.*,(SELECT count(*) FROM job_runtime_events e WHERE e.job_id=j.id AND e.action='retry') AS recorded_retries FROM jobs j WHERE meeting_id=? ORDER BY created DESC,j.rowid DESC", (row['id'],))
        row['revisions'] = self.store.all('SELECT revision,created,source FROM revisions WHERE meeting_id=? ORDER BY revision DESC', (row['id'],))
        artifacts = self.store.all('SELECT * FROM artifacts WHERE meeting_id=? ORDER BY created DESC,rowid DESC', (row['id'],))
        for a in artifacts:
            a['payload'] = json.loads(a['payload'])
            a['provenance'] = json.loads(a['provenance'] or '{}')
            a['stale'] = a['revision'] != row['revision']
        with self.store.connect() as db:
            for artifact in artifacts:
                if artifact['kind'] == 'meeting_minutes':
                    artifact['stale'] = artifact['stale'] or not meeting_flow.artifact_current(db, row['id'], artifact['provenance'])
        row['artifacts'] = artifacts
        notes = self.store.all('SELECT * FROM meeting_notes WHERE meeting_id=? ORDER BY captured_at,created,rowid', (row['id'],))
        for note in notes:
            note['source_segment_ids'] = json.loads(note['source_segment_ids'] or '[]')
            note['expansion_stale'] = bool(note['expansion_revision'] and note['expansion_revision'] != row['revision'])
            suggestion = next((a for a in artifacts if a['kind'] == 'note_expansion' and a['payload'].get('note_id') == note['id']), None)
            if suggestion:
                suggestion['stale'] = suggestion['stale'] or any((suggestion['payload'].get(key) != note[key] for key in ('raw_text', 'note_type', 'captured_at'))) or suggestion['payload'].get('note_scope', 'personal') != note['note_scope']
            note['expansion'] = suggestion
        row['notes'] = notes
        from .notes import fingerprint
        with self.store.connect() as db:
            todos = meeting_flow.tasks(db, row['id'])
        with self.store.connect() as db:
            adopted = minutes_versions.selected(db, row['id'], row['revision'])
        current_minutes = next((a for a in artifacts if adopted and a['id'] == adopted['id'] and (not a['stale'])), None)
        for note in notes:
            note['adopted_to_minutes'] = bool(current_minutes and any((item.get('note_id') == note['id'] and item.get('note_fingerprint') == fingerprint(note) for item in current_minutes['provenance'].get('adopted_notes', []))))
        with self.store.connect() as db:
            db.execute('BEGIN')
            for todo in todos:
                todo['source_segment_ids'] = json.loads(todo['source_segment_ids'])
        row['note_todos'] = todos
        with self.store.connect() as db:
            row['action_tasks'] = {a['id']: meeting_flow.action_bindings(db, row['id'], a) for a in self.store.all("SELECT * FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes'", (row['id'],))}
        row['deliveries'] = [minutes_delivery.public(d) for d in self.store.all('SELECT * FROM minutes_deliveries WHERE meeting_id=? ORDER BY created DESC,rowid DESC', (row['id'],))]
        row['audio_tracks'] = [name for name in ('system', 'microphone') if (self.store.directory(row['id']) / (name + '.wav')).is_file()]
        row['recording_quality'] = recording_quality_summary(self.recordings, row['id'], float(row['duration']))
        row['review'] = self.meeting_review.read(row['id'])
        row['flow'] = meeting_flow.flow(self.store, row['id'])
        row['minutes_generation'] = self.minutes_generation.status(row['id'])
        with self.store.connect() as db:
            selected = minutes_versions.selected(db, row['id'])
            row['active_minutes_id'] = selected['id'] if selected else None
        return row

    def public_artifact(self, a):
        a = dict(a)
        a['payload'] = json.loads(a['payload'])
        a['provenance'] = json.loads(a['provenance'] or '{}')
        if a['kind'] == 'meeting_minutes':
            with self.store.connect() as db:
                a['stale'] = not meeting_flow.artifact_current(db, a['meeting_id'], a['provenance']) or self.meeting(a['meeting_id'])['revision'] != a['revision']
        return a

    def recording_open(self,rid):
        try:return self.recordings.read(rid).get('state')!='completed' and not self.store.one('SELECT id FROM meetings WHERE id=?',(rid,))
        except (FileNotFoundError,ValueError):return False
