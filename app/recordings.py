"""Durable incremental MediaRecorder streams with idempotent publication."""
import hashlib, json, os, threading, time
from pathlib import Path
from .db import uid, encode

class RecordingStore:
    def __init__(self,store):
        self.store=store;self.root=store.root/'recordings';self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock()

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
            self.write(folder/'session.json',session);return session

    def list(self):
        with self.lock:
            rows=[]
            for path in self.root.glob('*/session.json'):
                value=json.loads(path.read_text())
                if value['state']!='completed' and not self.store.one('SELECT id FROM meetings WHERE id=?',(value['id'],)):
                    parts=list(path.parent.glob('*.part'))
                    value['saved_chunks']=len(parts)
                    value['chunk_count']=max(value['chunk_count'],max((int(p.stem)+1 for p in parts),default=0))
                    rows.append(value)
            return sorted(rows,key=lambda row:row['created'],reverse=True)

    def metadata(self,rid,notes,duration,chunk_count):
        with self.lock:
            value=self.read(rid)
            if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):return value
            value.update(notes=notes,duration=duration,chunk_count=chunk_count,updated=time.time())
            self.write(self.folder(rid)/'session.json',value);return value

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
            return {'ok':True,'index':index}

    def finish(self,rid,chunk_count):
        with self.lock:
            if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):return rid
            session=self.read(rid);folder=self.folder(rid)
            if session['state']=='completed':raise ValueError('此录音已完成并被删除，不能重新恢复')
            actual=sorted(folder.glob('*.part'))
            expected=[folder/f'{index:08d}.part' for index in range(chunk_count)]
            if not expected or actual!=expected:raise ValueError('录音分段尚未全部保存，请重试；已有分段已保留')
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
                    for note in session['notes']:
                        db.execute('INSERT INTO meeting_notes(id,meeting_id,captured_at,note_type,raw_text) VALUES(?,?,?,?,?)',
                            (uid(),rid,note['captured_at'],note['note_type'],note['raw_text']))
            session.update(state='completed',updated=time.time());self.write(folder/'session.json',session)
            for part in actual:part.unlink(missing_ok=True)
            return rid
