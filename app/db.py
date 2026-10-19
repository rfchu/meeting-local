import json, os, sqlite3, uuid
from contextlib import contextmanager
from pathlib import Path
from .minutes_templates import BUILTIN_TEMPLATES


def uid(): return uuid.uuid4().hex

def encode(value): return json.dumps(value, ensure_ascii=False)

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'meetings.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meetings (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
              created TEXT DEFAULT CURRENT_TIMESTAMP, duration REAL DEFAULT 0,
              revision INTEGER DEFAULT 0, segments TEXT DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS revisions (
              meeting_id TEXT, revision INTEGER, segments TEXT, source TEXT,
              created TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(meeting_id, revision));
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, meeting_id TEXT, state TEXT, stage TEXT,
              progress REAL DEFAULT 0, error TEXT DEFAULT '', config TEXT,
              created TEXT DEFAULT CURRENT_TIMESTAMP, kind TEXT NOT NULL DEFAULT 'transcription');
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, meeting_id TEXT, revision INTEGER,
              kind TEXT, payload TEXT, provenance TEXT,
              created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS meeting_reviews (
              meeting_id TEXT NOT NULL, step TEXT NOT NULL, fingerprint TEXT NOT NULL,
              decision TEXT NOT NULL, reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(meeting_id,step));
            CREATE TABLE IF NOT EXISTS llm_configs (
              id TEXT PRIMARY KEY, name TEXT NOT NULL,
              provider_type TEXT NOT NULL CHECK(provider_type IN ('local','cloud')),
              base_url TEXT NOT NULL, model TEXT NOT NULL, api_key TEXT NOT NULL DEFAULT '',
              is_default INTEGER NOT NULL DEFAULT 0,
              context_window INTEGER NOT NULL DEFAULT 32768,
              analysis_chunk_mode TEXT NOT NULL DEFAULT 'auto',
              analysis_chunk_chars INTEGER NOT NULL DEFAULT 0,
              created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS minutes_templates (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              category TEXT NOT NULL DEFAULT '通用', version INTEGER NOT NULL DEFAULT 1,
              builtin INTEGER NOT NULL DEFAULT 0, definition TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS meeting_notes (
              id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL,
              captured_at REAL NOT NULL DEFAULT 0,
              note_type TEXT NOT NULL DEFAULT 'note',
              raw_text TEXT NOT NULL, expanded_text TEXT NOT NULL DEFAULT '',
              accepted_text TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'raw',
              source_segment_ids TEXT NOT NULL DEFAULT '[]',
              created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS live_asr_ranges (
              id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, source TEXT NOT NULL,
              engine TEXT NOT NULL, start_frame INTEGER NOT NULL, end_frame INTEGER NOT NULL,
              audio_hash TEXT NOT NULL, identity TEXT NOT NULL, state TEXT NOT NULL,
              segments TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
              created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS live_asr_recording_range ON live_asr_ranges(recording_id,engine,start_frame);
            CREATE INDEX IF NOT EXISTS meeting_notes_meeting_time
              ON meeting_notes(meeting_id,captured_at,created);
            ''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'kind' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'transcription'")
            note_columns={row['name'] for row in db.execute('PRAGMA table_info(meeting_notes)')}
            if 'expansion_revision' not in note_columns:db.execute('ALTER TABLE meeting_notes ADD COLUMN expansion_revision INTEGER NOT NULL DEFAULT 0')
            if 'note_scope' not in note_columns:db.execute("ALTER TABLE meeting_notes ADD COLUMN note_scope TEXT NOT NULL DEFAULT 'personal'")
            if 'is_marker' not in note_columns:db.execute('ALTER TABLE meeting_notes ADD COLUMN is_marker INTEGER NOT NULL DEFAULT 0')
            db.execute('''CREATE TABLE IF NOT EXISTS note_todos (
                id TEXT PRIMARY KEY,meeting_id TEXT NOT NULL,note_id TEXT NOT NULL,content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',owner TEXT NOT NULL DEFAULT '',due_date TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL,note_fingerprint TEXT NOT NULL,source_segment_ids TEXT NOT NULL DEFAULT '[]',
                note_scope TEXT NOT NULL DEFAULT 'personal',created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP)''')
            config_columns={row['name'] for row in db.execute('PRAGMA table_info(llm_configs)')}
            if 'context_window' not in config_columns:
                db.execute('ALTER TABLE llm_configs ADD COLUMN context_window INTEGER NOT NULL DEFAULT 32768')
            if 'analysis_chunk_mode' not in config_columns:
                db.execute("ALTER TABLE llm_configs ADD COLUMN analysis_chunk_mode TEXT NOT NULL DEFAULT 'auto'")
            if 'analysis_chunk_chars' not in config_columns:
                db.execute('ALTER TABLE llm_configs ADD COLUMN analysis_chunk_chars INTEGER NOT NULL DEFAULT 0')
            for template in BUILTIN_TEMPLATES:
                definition=encode(template.model_dump())
                db.execute('''INSERT INTO minutes_templates(id,name,description,category,version,builtin,definition,status)
                    VALUES(?,?,?,?,?,?,?,'active') ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,description=excluded.description,category=excluded.category,
                    version=excluded.version,builtin=excluded.builtin,definition=excluded.definition,
                    updated=CURRENT_TIMESTAMP''',
                    (template.id,template.name,template.description,template.category,template.version,1,definition))
            db.execute('PRAGMA user_version=11')
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
