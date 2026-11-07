"""Local organization metadata; the backup system snapshots this SQLite sidecar."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class LibraryUpdate(BaseModel):
    meeting_ids: list[str] = Field(min_length=1, max_length=100)
    group: str | None = Field(default=None, max_length=100)
    group_kind: Literal['project', 'customer', 'series'] = 'series'
    archived: bool | None = None


def entries(root):
    path = Path(root) / 'meeting-library.sqlite3'
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='organization' AND type='table'").fetchone():
            return {}
        return {row['meeting_id']: dict(row) for row in db.execute('SELECT * FROM organization')}


def update(store, body):
    ids = list(dict.fromkeys(body.meeting_ids))
    if body.group is None and body.archived is None:
        raise ValueError('请选择归类或归档操作')
    with store.connect() as meetings:
        # Keep the selected meetings stable while the metadata transaction commits.
        meetings.execute('BEGIN IMMEDIATE')
        params = ','.join('?' for _ in ids)
        count = meetings.execute(f'SELECT count(*) FROM meetings WHERE id IN ({params})', ids).fetchone()[0]
        if count != len(ids):
            raise KeyError('会议不存在')
        path = store.root / 'meeting-library.sqlite3'
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""CREATE TABLE IF NOT EXISTS organization (
                meeting_id TEXT PRIMARY KEY, group_name TEXT NOT NULL DEFAULT '',
                group_kind TEXT NOT NULL DEFAULT 'series', archived INTEGER NOT NULL DEFAULT 0)""")
            db.executemany('INSERT OR IGNORE INTO organization(meeting_id) VALUES(?)', [(mid,) for mid in ids])
            if body.group is not None:
                db.executemany('UPDATE organization SET group_name=?,group_kind=? WHERE meeting_id=?',
                               [(body.group.strip(), body.group_kind, mid) for mid in ids])
            if body.archived is not None:
                db.executemany('UPDATE organization SET archived=? WHERE meeting_id=?',
                               [(body.archived, mid) for mid in ids])
    return {'updated': len(ids)}
