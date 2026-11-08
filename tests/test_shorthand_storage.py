"""Tests for shorthand note storage adaptations in RecordingStore and Store."""
import json
import time
from pathlib import Path
import pytest

from app.db import Store
from app.recordings import RecordingStore
from app.notes import insert_note, ensure_note_columns


def test_recording_store_add_raw_shorthand_note(tmp_path):
    store = Store(tmp_path / 'data')
    recordings = RecordingStore(store)
    session = recordings.create('audio/webm')
    rid = session['id']

    note_data = {
        'raw_text': 'db RC 压测周五',
        'captured_at': 15.2,
        'note_type': 'shorthand',
        'speaker_hint': '张三',
    }

    created = recordings.add_note(rid, note_data)
    assert created['id']
    assert created['raw_text'] == 'db RC 压测周五'
    assert created['captured_at'] == 15.2
    assert created['status'] == 'raw'
    assert created['speaker_hint'] == '张三'
    assert created['synthesized_text'] == ''
    assert created['anchor_start'] is None
    assert created['anchor_end'] is None

    # Check persistence in session.json
    fresh = recordings.read(rid)
    assert len(fresh['notes']) == 1
    assert fresh['notes'][0]['id'] == created['id']
    assert fresh['notes'][0]['raw_text'] == 'db RC 压测周五'


def test_recording_store_update_shorthand_note(tmp_path):
    store = Store(tmp_path / 'data')
    recordings = RecordingStore(store)
    session = recordings.create('audio/webm')
    rid = session['id']

    created = recordings.add_note(rid, {
        'raw_text': 'db RC 压测周五',
        'captured_at': 15.2,
    })
    nid = created['id']

    # Update note with synthesized result
    updated = recordings.update_note(
        rid,
        nid,
        status='ready',
        synthesized_text='【架构决策】确认数据库隔离级别采用 Read Committed，预定于周五完成高并发压测。',
        anchor_start=12.0,
        anchor_end=18.5,
        speaker_hint='张三',
    )

    assert updated['id'] == nid
    assert updated['status'] == 'ready'
    assert 'Read Committed' in updated['synthesized_text']
    assert updated['anchor_start'] == 12.0
    assert updated['anchor_end'] == 18.5
    assert updated['speaker_hint'] == '张三'

    # Check persistence
    fresh = recordings.read(rid)
    saved_note = next(n for n in fresh['notes'] if n['id'] == nid)
    assert saved_note['status'] == 'ready'
    assert saved_note['synthesized_text'] == updated['synthesized_text']


def test_shorthand_notes_publish_to_sqlite(tmp_path):
    store = Store(tmp_path / 'data')
    recordings = RecordingStore(store)
    session = recordings.create('audio/webm')
    rid = session['id']

    recordings.add_note(rid, {
        'id': 'note1',
        'raw_text': '普通文本笔记',
        'captured_at': 5.0,
        'status': 'raw',
    })
    recordings.add_note(rid, {
        'id': 'note2',
        'raw_text': 'db RC 压测周五',
        'synthesized_text': '【架构决策】确认数据库隔离级别采用 Read Committed。',
        'captured_at': 15.2,
        'anchor_start': 12.0,
        'anchor_end': 18.5,
        'speaker_hint': '张三',
        'status': 'ready',
    })

    # Add a mock chunk and finish recording to trigger publish
    recordings.chunk(rid, 0, b'mock-webm-chunk-data')
    recordings.finish(rid, 1)

    # Verify notes in SQLite
    with store.connect() as db:
        ensure_note_columns(db)
        rows = db.execute('SELECT * FROM meeting_notes WHERE meeting_id=? ORDER BY captured_at', (rid,)).fetchall()
        assert len(rows) == 2
        n1 = dict(rows[0])
        n2 = dict(rows[1])

        assert n1['id'] == 'note1'
        assert n1['raw_text'] == '普通文本笔记'
        assert n1['status'] == 'raw'

        assert n2['id'] == 'note2'
        assert n2['raw_text'] == 'db RC 压测周五'
        assert n2['synthesized_text'] == '【架构决策】确认数据库隔离级别采用 Read Committed。'
        assert n2['anchor_start'] == 12.0
        assert n2['anchor_end'] == 18.5
        assert n2['speaker_hint'] == '张三'
        assert n2['status'] == 'ready'


def test_copilot_note_survives_browser_sync_and_retry(tmp_path):
    store = Store(tmp_path)
    recordings = RecordingStore(store)
    rid = recordings.create('audio/webm')['id']
    data = {'client_id': 'stable-request', 'raw_text': '保留这一条'}
    note = recordings.add_note(rid, data)
    recordings.metadata(rid, [], 12, 1)
    assert recordings.add_note(rid, data)['id'] == note['id']
    assert recordings.read(rid)['notes'] == [note]
    recordings.chunk(rid, 0, b'synthetic audio')
    recordings.finish(rid, 1)
    assert recordings.add_note(rid, data)['id'] == note['id']
    assert store.one('SELECT COUNT(*) AS n FROM meeting_notes WHERE meeting_id=?', (rid,))['n'] == 1


def test_interrupted_synthesis_recovers_original_for_live_and_published_notes(tmp_path):
    store = Store(tmp_path)
    recordings = RecordingStore(store)
    live = recordings.create('audio/webm')['id']
    completed = recordings.create('audio/webm')['id']
    notes = {}
    for rid in (live, completed):
        notes[rid] = recordings.add_note(rid, {'raw_text': '应保留', 'status': 'synthesizing'})
    recordings.metadata(completed, [], 12, 1)
    recordings.chunk(completed, 0, b'synthetic audio')
    recordings.finish(completed, 1)
    recordings.recover_shorthand()
    for rid in (live, completed):
        recovered = recordings.get_note(rid, notes[rid]['id'])
        assert recovered['status'] == 'failed'
        assert recovered['raw_text'] == '应保留'
