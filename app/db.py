import json, os, sqlite3, uuid
from contextlib import contextmanager
from pathlib import Path
from .maintenance import DataLease


def uid(): return uuid.uuid4().hex

def encode(value): return json.dumps(value, ensure_ascii=False)

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with DataLease(self.root):self._initialize()
    def _initialize(self):
        from .migrations import initialize_store
        self.path = self.root / 'meetings.sqlite3'
        initialize_store(self.path)
        os.chmod(self.path,0o600)
    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally: db.close()
    def one(self, sql, args=()):
        with self.connect() as db:
            row=db.execute(sql,args).fetchone()
            return dict(row) if row else None
    def all(self, sql, args=()):
        with self.connect() as db: return [dict(x) for x in db.execute(sql,args)]
    def execute(self, sql, args=()):
        with self.connect() as db: db.execute(sql,args)
    def directory(self, mid):
        if len(mid)!=32 or any(c not in '0123456789abcdef' for c in mid): raise ValueError('Invalid meeting ID')
        return self.root / mid
    def save_revision(self, mid, segments, source, expected=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            if not row: raise KeyError(mid)
            if expected is not None and row['revision'] != expected: raise ValueError('文稿已更新，请刷新后再保存')
            rev=row['revision']+1
            payload=encode(segments)
            db.execute('INSERT INTO revisions(meeting_id,revision,segments,source) VALUES(?,?,?,?)',(mid,rev,payload,source))
            db.execute('UPDATE meetings SET revision=?,segments=? WHERE id=?',(rev,payload,mid))
            return rev
