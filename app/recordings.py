"""Durable incremental MediaRecorder streams with idempotent publication."""
import hashlib, json, os, threading, time
from pathlib import Path
from .db import uid, encode

class RecordingStore:
    def __init__(self,store):
        self.store=store;self.root=store.root/'recordings';self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.lifecycle=None

    def event(self,code,rid,**metrics):
        if self.lifecycle:self.lifecycle.record(code,rid,**metrics)

    def folder(self,rid):
        self.store.directory(rid) # Validate before resolving any recording path.
        folder=self.root/rid
        if not (folder/'session.json').exists():raise FileNotFoundError('录音会话不存在')
        return folder

    def read(self,rid):return json.loads((self.folder(rid)/'session.json').read_text())

    @staticmethod
    def sync_directory(path):
        descriptor=os.open(path,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)

    @staticmethod
    def write(path,value):
        temporary=path.with_suffix('.tmp')
        with temporary.open('w') as file:
            file.write(encode(value));file.flush();os.fsync(file.fileno())
        temporary.replace(path)
        RecordingStore.sync_directory(path.parent)

    def create(self,mime_type):
        with self.lock:
            rid=uid();folder=self.root/rid;folder.mkdir()
            session={'id':rid,'mime_type':mime_type,'created':time.time(),'updated':time.time(),
                     'duration':0,'notes':[],'chunk_count':0,'state':'recording'}
            self.write(folder/'session.json',session);self.event('recording.created',rid);return session

    def list(self):
        with self.lock:
            rows=[]
            for path in self.root.glob('*/session.json'):
                value=json.loads(path.read_text())
                if value['state']!='completed' and not self.store.one('SELECT id FROM meetings WHERE id=?',(value['id'],)):
                    parts=list(path.parent.glob('*.part'))
                    value['saved_chunks']=len(parts)
                    value['chunk_count']=max(value['chunk_count'],max((int(p.stem)+1 for p in parts),default=0))
                    if value.get('source')=='macos':
                        try:
                            capture=json.loads((path.parent/'capture.json').read_text())
                            value['duration']=capture.get('duration',value['duration'])
                        except (FileNotFoundError,ValueError):pass
                        value['native_audio_saved']=any(p.stat().st_size>0 for p in path.parent.glob('*.pcm'))
                    rows.append(value)
            return sorted(rows,key=lambda row:row['created'],reverse=True)

    def metadata(self,rid,notes,duration,chunk_count,brief=None):
        with self.lock:
            value=self.read(rid)
            if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):return value
            if brief is not None:value['brief']=brief
            server_notes=[note for note in value['notes'] if 'status' in note]
            value.update(notes=notes+server_notes,duration=duration,chunk_count=chunk_count,updated=time.time())
            self.write(self.folder(rid)/'session.json',value);return value

    def recover_shorthand(self):
        """Keep the original note usable after an interrupted inference."""
        with self.lock:
            self.store.execute("UPDATE meeting_notes SET status='failed' WHERE status='synthesizing'")
            for session in self.list():
                if not any(note.get('status') == 'synthesizing' for note in session['notes']):
                    continue
                value = self.read(session['id'])
                for note in value['notes']:
                    if note.get('status') == 'synthesizing':
                        note['status'] = 'failed'
                self.write(self.folder(session['id']) / 'session.json', value)

    def dismiss_candidate(self, rid, candidate_id):
        with self.lock:
            session = self.read(rid)
            dismissed = session.setdefault('dismissed_candidates', [])
            if candidate_id not in dismissed:
                dismissed.append(candidate_id)
                self.write(self.folder(rid) / 'session.json', session)

    def add_note(self, rid, note):
        with self.lock:
            value = self.read(rid)
            client_id = note.get('client_id')
            published = self.store.one('SELECT id FROM meetings WHERE id=?', (rid,))
            if client_id:
                existing = (self.store.one('SELECT * FROM meeting_notes WHERE meeting_id=? AND client_id=?', (rid, client_id))
                            if published else next((row for row in value['notes'] if row.get('client_id') == client_id), None))
                if existing:
                    if existing['raw_text'] != note['raw_text']:
                        raise ValueError('速记请求编号已用于其他内容')
                    return existing
            nid = note.get('id') or uid()
            now = time.time()
            entry = {
                'id': nid,
                'captured_at': float(note.get('captured_at', 0)),
                'raw_text': str(note.get('raw_text', '')).strip(),
                'note_type': note.get('note_type', 'note'),
                'note_scope': note.get('note_scope', 'personal'),
                'is_marker': bool(note.get('is_marker', False)),
                'client_id': note.get('client_id', ''),
                'overview_focus': bool(note.get('overview_focus', False)),
                'status': note.get('status', 'raw'),
                'synthesized_text': note.get('synthesized_text', ''),
                'anchor_start': note.get('anchor_start'),
                'anchor_end': note.get('anchor_end'),
                'speaker_hint': note.get('speaker_hint', ''),
                'shorthand_quote': '',
                'shorthand_track': note.get('shorthand_track', ''),
                'copilot': True,
                'candidate_id': note.get('candidate_id', ''),
                'owner': note.get('owner', ''),
                'due_date': note.get('due_date', ''),
                'created': now,
                'updated': now,
            }
            if published:
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    from .notes import insert_note
                    insert_note(db, rid, entry)
                return entry

            notes = value.get('notes', [])
            notes.append(entry)
            value['notes'] = notes
            value['updated'] = now
            self.write(self.folder(rid) / 'session.json', value)
            return entry

    def update_note(self, rid, nid, *, expected_raw=None, **updates):
        with self.lock:
            now = time.time()
            if self.store.one('SELECT id FROM meetings WHERE id=?', (rid,)):
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    from .notes import ensure_note_columns
                    ensure_note_columns(db)
                    if expected_raw is not None:
                        current = db.execute(
                            "SELECT status, raw_text FROM meeting_notes WHERE id=? AND meeting_id=?", (nid, rid)
                        ).fetchone()
                        if not current or current['status'] != 'synthesizing' or current['raw_text'] != expected_raw:
                            return None
                    allowed = {
                        'raw_text', 'synthesized_text', 'status',
                        'anchor_start', 'anchor_end', 'speaker_hint', 'shorthand_quote', 'shorthand_track',
                        'note_type', 'note_scope', 'overview_focus'
                    }
                    fields_to_set = {k: v for k, v in updates.items() if k in allowed}
                    if fields_to_set:
                        set_clause = ', '.join(f'{k}=?' for k in fields_to_set)
                        db.execute(
                            f'UPDATE meeting_notes SET {set_clause}, updated=CURRENT_TIMESTAMP WHERE id=? AND meeting_id=?',
                            (*fields_to_set.values(), nid, rid)
                        )
                    row = db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?', (nid, rid)).fetchone()
                    return dict(row) if row else None

            value = self.read(rid)
            notes = value.get('notes', [])
            target = None
            for item in notes:
                if item.get('id') == nid:
                    if expected_raw is not None and (item['status'] != 'synthesizing' or item['raw_text'] != expected_raw):
                        return None
                    item.update(updates)
                    item['updated'] = now
                    target = item
                    break
            if target is None:
                raise KeyError(f'Note {nid} not found in recording {rid}')
            value['updated'] = now
            self.write(self.folder(rid) / 'session.json', value)
            return target

    def copilot_notes(self, rid):
        with self.lock:
            notes = [note for note in self.read(rid)['notes'] if 'status' in note]
            if not self.store.one('SELECT id FROM meetings WHERE id=?', (rid,)):
                return notes
            published = {row['id']: row for row in self.store.all(
                'SELECT * FROM meeting_notes WHERE meeting_id=?', (rid,)
            )}
            return [{**note, **published[note['id']]} for note in notes if note['id'] in published]

    def get_note(self, rid, nid):
        if self.store.one('SELECT id FROM meetings WHERE id=?', (rid,)):
            return self.store.one('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?', (nid, rid))
        value = self.read(rid)
        for item in value.get('notes', []):
            if item.get('id') == nid:
                return item
        return None

    def chunk(self,rid,index,payload):
        with self.lock:
            folder=self.folder(rid);target=folder/f'{index:08d}.part'
            if target.exists():
                if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(payload).digest():raise ValueError('分段编号冲突，已有录音已保留')
                return {'ok':True,'index':index}
            if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):raise ValueError('录音已完成')
            if sum(p.stat().st_size for p in folder.glob('*.part'))+len(payload)>4*1024**3:raise ValueError('录音最大支持 4GB')
            temporary=target.with_suffix('.tmp')
            with temporary.open('wb') as file:
                file.write(payload);file.flush();os.fsync(file.fileno())
            temporary.replace(target)
            self.sync_directory(folder)
            self.event('storage.available',rid,saved_bytes=len(payload))
            return {'ok':True,'index':index}

    def finish(self,rid,chunk_count):
        with self.lock:
            if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):return rid
            session=self.read(rid);folder=self.folder(rid)
            if session['state']=='completed':raise ValueError('此录音已完成并被删除，不能重新恢复')
            actual=sorted(folder.glob('*.part'))
            expected=[folder/f'{index:08d}.part' for index in range(chunk_count)]
            if not expected or actual!=expected:raise ValueError('录音分段尚未全部保存，请重试；已有分段已保留')
            if any(part.stat().st_size==0 for part in actual):raise ValueError('录音分段为空，请重新同步；已有分段已保留')
            target=self.store.directory(rid);target.mkdir(exist_ok=True)
            temporary=target/'original.tmp';digest=hashlib.sha256();size=0
            with temporary.open('wb') as output:
                for part in actual:
                    with part.open('rb') as source:
                        while block:=source.read(1024*1024):output.write(block);digest.update(block);size+=len(block)
                output.flush();os.fsync(output.fileno())
            temporary.replace(target/'original')
            self.sync_directory(target)
            extension='m4a' if 'mp4' in session['mime_type'] else 'webm'
            filename=time.strftime('会议录音-%Y%m%d-%H%M%S',time.localtime(session['created']))+'.'+extension
            self.write(target/'asset.json',{'filename':filename,'size':size,'sha256':digest.hexdigest(),'recording_session':rid})
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT id FROM meetings WHERE id=?',(rid,)).fetchone():
                    db.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(rid,Path(filename).stem,filename,session['duration']))
                    from .meeting_flow import publish_preparation
                    publish_preparation(db,rid,session.get('brief'))
                    for note in session['notes']:
                        from .notes import insert_note
                        insert_note(db,rid,note)
            session.update(state='completed',updated=time.time());self.write(folder/'session.json',session)
            self.event('storage.completed',rid,saved_bytes=size,duration_seconds=session['duration'])
            for part in actual:part.unlink(missing_ok=True)
            return rid
