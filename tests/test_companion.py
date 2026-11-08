import pytest
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


@pytest.mark.parametrize(('language', 'text'), [
    ('en', 'We will verify the latest recording.'),
    ('ja', '最新の録音を確認します。'),
])
def test_bilingual_reads_only_the_latest_confirmed_text_and_cached_translation(tmp_path, monkeypatch, language, text):
    from types import SimpleNamespace
    from app.chinese_record import translation_file, write_translation

    app = create_app(tmp_path, start_worker=False)
    services = app.state.services
    rid = services.recordings.create('audio/webm')['id']
    session = services.recordings.read(rid)
    services.recordings.write(services.recordings.folder(rid)/'session.json', {**session, 'source_language': language})
    original_read = services.recording_runtime.read

    def runtime(*args):
        value = original_read(*args)
        value['asr']['engine'] = 'qwen'
        return value

    monkeypatch.setattr(services.recording_runtime, 'read', runtime)
    latest = {'start': 2, 'end': 4, 'text': text}
    earlier = {'start': 0, 'end': 2, 'text': 'This is earlier.'}
    monkeypatch.setattr(services.live_results, 'snapshot',
                        lambda *args: [{'segments': [latest, earlier]}])
    provider = SimpleNamespace(profile={'id': 'local'}, base_url='http://127.0.0.1:1234',
                               resolve_model=lambda: 'model')
    monkeypatch.setattr(services.retrieval, 'translation_providers', lambda db: [provider])
    client = TestClient(app)
    params = {'mode': 'bilingual', 'rid': rid}
    path = '/api/runtime/companion'
    assert client.get(path, params=params).json()['snapshot']['lines'] == [latest['text'], '等待译文']
    write_translation(translation_file(services.store.directory(rid), latest, language, provider), '我们会核对最新的录音。')
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


def test_companion_exposes_only_current_native_track_levels(tmp_path, monkeypatch):
    import time
    from app.companion import snapshot

    app = create_app(tmp_path, start_worker=False)
    services = app.state.services
    rid = services.recordings.create('audio/webm')['id']
    monkeypatch.setattr(services.recording_runtime, 'read', lambda _: {
        'recording_id': rid, 'identity': {'meeting_id': None}, 'source': 'native',
        'phase': 'recording', 'storage': {'state': 'writing'},
        'asr': {'state': 'running'}, 'capture': {'timeline_seconds': 62},
    })
    path = services.recordings.folder(rid) / 'capture.json'
    report = {'state': 'recording', 'updated': time.time(), 'system_levels': [.1] * 30,
              'microphone_levels': [.8, .3], 'buffer_age': {'system': .1, 'microphone': 2}}
    services.recordings.write(path, report)
    view = snapshot(services, 'status', rid)
    assert view['duration_seconds'] == 62
    assert view['system_levels'] == [.1] * 24
    assert view['microphone_levels'] == [0, 0]
    services.recordings.write(path, {**report, 'updated': time.time() - 60})
    assert 'system_levels' not in snapshot(services, 'status', rid)


def test_companion_follows_fresh_recording_past_abandoned_browser_session(tmp_path):
    import time

    app = create_app(tmp_path, start_worker=False)
    recordings = app.state.services.recordings
    client = TestClient(app)
    path = '/api/runtime/companion'
    first = recordings.create('audio/webm')
    assert client.get(path).json()['snapshot']['recording_id'] == first['id']

    first['updated'] = time.time() - 120
    recordings.write(recordings.folder(first['id'])/'session.json', first)
    second = recordings.create('audio/webm')
    assert client.get(path).json()['snapshot']['recording_id'] == second['id']
    assert client.get(path, params={'rid': first['id']}).json()['snapshot']['phase'] == 'unknown'


def test_companion_subtitles_follow_live_drafts_and_translate_without_workbench(tmp_path, monkeypatch):
    from app.analysis import OpenAICompatibleProvider
    from app.db import encode, uid

    app = create_app(tmp_path, start_worker=False)
    services = app.state.services
    rid = services.recordings.create('audio/webm')['id']
    profile_id = uid()
    profile = {'id': profile_id, 'provider_type': 'local', 'model': 'local-test',
               'base_url': 'http://127.0.0.1:1234/v1', 'api_key': ''}
    monkeypatch.setattr(type(services.configurations), 'get', lambda _, __: profile)
    monkeypatch.setattr(services.retrieval, 'translation_providers',
                        lambda _: [OpenAICompatibleProvider(profile)])
    calls = []
    def translate(_, source):
        calls.append(source)
        return {'translation': '我们确认了交付日期。'}
    monkeypatch.setattr('app.chinese_record.request_translation', translate)
    source = {'start': 0, 'end': 5, 'text': 'We confirmed the delivery date.', 'source_track': 'system'}
    aid, _ = services.live_results.begin(rid, 'browser', 'qwen', 0, 5, b'fixture', {})
    services.live_results.complete(aid, [source])
    partial = {'tracks': [
        {'start': 5, 'end': 8, 'text': 'Our next topic is staffing.', 'stable_text': 'Our next topic', 'source_track': 'system'},
        {'start': 5, 'end': 8, 'text': '我来说明。', 'source_track': 'microphone'},
    ]}
    services.store.execute('INSERT INTO live_sessions(recording_id,source,config,seq,partial) VALUES(?,?,?,?,?)',
                           (rid, 'browser', encode({'engine': 'qwen'}), 7, encode(partial)))
    client = TestClient(app)
    path = '/api/runtime/companion'
    original = client.get(path, params={'mode': 'original'}).json()['snapshot']
    assert original['subtitle_seq'] == 7
    assert original['drafts'][0]['stable_text'] == 'Our next topic'
    assert original['drafts'][1]['source_track'] == 'microphone'
    assert 'translation_request' not in original
    bilingual = client.get(path, params={'mode': 'bilingual'}).json()['snapshot']
    reference = bilingual['translation_request']
    assert reference == {'aid': aid, 'index': 0, 'profile_id': profile_id}
    assert not calls
    claim = services.resources.request('live_translation', {'local_llm': 1}, rid)
    assert claim.acquire(blocking=False)
    try:
        assert client.post(path + '/translation', params={'rid': rid, **reference}).status_code == 429
        assert client.get(path, params={'mode': 'bilingual'}).json()['snapshot']['drafts'] == original['drafts']
        assert not calls
    finally:
        claim.close()
    for _ in range(2):
        assert client.post(path + '/translation', params={'rid': rid, **reference}).status_code == 204
    assert calls == [source['text']]
    bilingual = client.get(path, params={'mode': 'bilingual'}).json()['snapshot']
    assert bilingual['lines'] == [source['text'], '我们确认了交付日期。']
    assert bilingual['drafts'] == original['drafts']
    assert 'translation_request' not in bilingual


def test_companion_subscription_wakes_on_same_live_event(tmp_path):
    import asyncio

    app = create_app(tmp_path, start_worker=False)
    services = app.state.services
    manager = services.live_sessions
    rid = services.recordings.create('audio/webm')['id']
    services.store.execute("INSERT INTO live_sessions(recording_id,source,config) VALUES(?,?,?)",
                           (rid, 'browser', '{"engine":"qwen"}'))

    async def verify():
        async with asyncio.timeout(.5):
            waiter = asyncio.create_task(manager.wait_for_change(rid, 0))
            await asyncio.sleep(0)
            assert not waiter.done()
            manager.emit(rid, 'partial', partial={'text': '正在发言'})
            await waiter
            await manager.wait_for_change(rid, 0)  # reconnect behind the current sequence
    asyncio.run(verify())
