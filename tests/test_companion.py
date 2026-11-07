from fastapi.testclient import TestClient
from app.main import create_app


def test_companion_observes_only_and_pins_a_single_recording(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_worker=False)
    services = app.state.services
    client = TestClient(app)
    path = '/api/runtime/companion'
    assert client.get(path).json()['snapshot']['phase'] == 'idle'
    first = services.recordings.create('audio/webm')['id']
    original = services.recordings.read(first)

    def fail_if_called(*args):
        raise AssertionError('status mode must not read meeting content')

    monkeypatch.setattr(services.live_overview, 'read', fail_if_called)
    monkeypatch.setattr(services.live_results, 'snapshot', fail_if_called)
    payload = client.get(path).json()
    assert payload['host']['capture_ownership']['host'] == 'none'
    assert payload['snapshot']['recording_id'] == first
    assert payload['snapshot']['phase'] == 'recording'
    assert payload['snapshot']['lines'] == []
    second = services.recordings.create('audio/webm')['id']
    assert client.get(path).json()['snapshot']['phase'] == 'multiple'
    monkeypatch.setattr(services.live_overview, 'read', lambda rid: {'items': [
        {'content': rid, 'stale': False, 'decision': 'pending', 'evidence': [{'start': 0}]},
        {'content': 'older decision', 'stale': False, 'decision': 'pending', 'evidence': [{'start': 10}]},
        {'content': 'earlier action', 'stale': False, 'decision': 'pending', 'evidence': [{'start': 20}]},
        {'content': 'updated decision', 'stale': False, 'decision': 'pending', 'evidence': [{'start': 5}, {'start': 35}]},
        {'content': 'stale', 'stale': True, 'decision': 'pending', 'evidence': [{'start': 40}]},
        {'content': 'dismissed', 'stale': False, 'decision': 'dismissed', 'evidence': [{'start': 45}]},
    ]})
    assert client.get(path, params={'rid': first, 'mode': 'highlights'}).json()['snapshot']['lines'] == [
        'updated decision', 'earlier action', 'older decision',
    ]
    assert client.get(path, params={'rid': '../x'}).status_code == 422
    assert client.post(path).status_code == 405
    assert services.recordings.read(first) == original
    assert {r['id'] for r in services.recordings.list()} == {first, second}


def test_bilingual_reads_only_the_latest_confirmed_text_and_cached_translation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.chinese_record import translation_file, write_translation

    app = create_app(tmp_path, start_worker=False)
    services = app.state.services
    rid = services.recordings.create('audio/webm')['id']
    original_read = services.recording_runtime.read

    def runtime(*args):
        value = original_read(*args)
        value['asr']['engine'] = 'qwen'
        return value

    monkeypatch.setattr(services.recording_runtime, 'read', runtime)
    latest = {'start': 2, 'end': 4, 'text': 'We will verify the latest recording.'}
    earlier = {'start': 0, 'end': 2, 'text': 'This is earlier.'}
    monkeypatch.setattr(services.live_results, 'snapshot',
                        lambda *args: [{'segments': [latest, earlier]}])
    provider = SimpleNamespace(profile={'id': 'local'}, base_url='http://127.0.0.1:1234',
                               resolve_model=lambda: 'model')
    monkeypatch.setattr(services.retrieval, 'translation_providers', lambda db: [provider])
    client = TestClient(app)
    params = {'mode': 'bilingual', 'rid': rid}
    path = '/api/runtime/companion'
    assert client.get(path, params=params).json()['snapshot']['lines'] == [latest['text'], '等待工作台译文']
    write_translation(translation_file(services.store.directory(rid), latest, 'en', provider), '我们会核对最新的录音。')
    assert client.get(path, params=params).json()['snapshot']['lines'] == [latest['text'], '我们会核对最新的录音。']


def test_host_diagnostics_separates_running_backend_from_disk_and_detects_active_recording(tmp_path, monkeypatch):
    from app.routes import runtime

    fingerprint = {'value': 'running'}
    monkeypatch.setattr(runtime, 'source_digest', lambda _: fingerprint['value'])
    app = create_app(tmp_path, start_worker=False)
    fingerprint['value'] = 'updated'
    monkeypatch.setattr(runtime, 'inspect_bundle', lambda *_: {'verified': True, 'version': '4'})
    with TestClient(app) as client:
        path = '/api/runtime/host-diagnostics'
        before = client.get(path).json()
        assert before['running_backend'] == 'running' and before['disk_backend'] == 'updated'
        assert before['busy'] is False
        assert before['frontend']['verified'] and before['capture']['verified']
        response = client.post('/api/recordings', json={'mime_type': 'audio/webm'})
        assert response.status_code == 201
        assert client.get(path).json()['busy'] is True
