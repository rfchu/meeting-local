import json

from fastapi.testclient import TestClient

from app import backups, meeting_library
from app.db import Store, encode, uid
from app.main import create_app
from app.maintenance import DataLease
from app.minutes import MeetingMinutes, MinutesItem
from app.paths import RuntimePaths
from app.workspace_readiness import migration_plan
import pytest


def add_meeting(store):
    mid = uid()
    store.directory(mid).mkdir()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)', (mid, '验收会议', 'test.wav'))
    return mid


def test_library_batch_undo_and_restore_preserve_metadata(tmp_path):
    source, bundle, target = (tmp_path / name for name in ('data', 'backup', 'restored'))
    app = create_app(source, start_worker=False)
    with TestClient(app) as client:
        ids = [add_meeting(app.state.store) for _ in range(2)]
        path = '/api/meeting-library'
        assert client.put(path, json={'meeting_ids': ids, 'group_kind': 'customer', 'group': '客户 A', 'archived': True}).json() == {'updated': 2}
        assert client.put(path, json={'meeting_ids': [ids[0], 'missing'], 'archived': False}).status_code == 404
        assert all(row['library']['archived'] for row in client.get('/api/meetings').json())
        assert client.put(path, json={'meeting_ids': ids, 'archived': False}).status_code == 200
        paths = client.get('/api/runtime/workspace').json()
        assert next(p for p in paths['paths'] if p['id'] == 'data')['path'] == str(source)
    assert migration_plan(source, bundle, target)['ready']
    assert not bundle.exists() and not target.exists()
    with DataLease(source), pytest.raises(RuntimeError):
        with backups.offline(source):
            pytest.fail('active runtime must block offline work')
    with backups.offline(source) as lease:
        backups.create_backup(source, bundle, maintenance=lease)
        backups.restore_backup(bundle, target)
    assert meeting_library.entries(target) == meeting_library.entries(source)
    assert len(Store(target).all('SELECT id FROM meetings')) == 2
    assert all(not row['archived'] for row in meeting_library.entries(target).values())


def test_selected_minutes_draft_preserves_adopted_version_and_both_sources(tmp_path):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        store = app.state.store
        mid = add_meeting(store)
        store.save_revision(mid, [{'id': 's', 'start': 0, 'end': 5, 'speaker': '甲', 'text': '下周交付'}], 'manual')
        left, right = uid(), uid()
        doc = MeetingMinutes(title='周会', style='ai', decisions=[MinutesItem(content='下周交付', source_segment_ids=['s'])])
        for aid in (left, right):
            store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                          (aid, mid, 1, 'meeting_minutes', encode(doc.model_dump()), '{}'))
        base = f'/api/meetings/{mid}/minutes'
        assert client.post(f'{base}/{left}/adopt').status_code == 200
        body = {'source_minutes_id': left, 'merge_from_minutes_id': right,
                'allow_branch': True, 'document': doc.model_dump()}
        result = client.put(base, json=body)
        assert result.status_code == 201, result.text
        draft = result.json()
        assert draft['provenance']['draft']
        assert draft['provenance']['source_minutes_dependencies'] == [left, right]
        assert client.get(f'/api/meetings/{mid}').json()['active_minutes_id'] == left
        assert json.loads(store.one('SELECT payload FROM artifacts WHERE id=?', (left,))['payload']) == doc.model_dump()
        assert client.post(f"{base}/{draft['id']}/adopt").status_code == 200
        assert client.put(base, json=body).status_code == 409
