import io
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient

from app import analysis
from app.main import create_app


def test_connection_test_uses_model_catalog_and_does_not_reuse_key_across_destinations(tmp_path, monkeypatch):
    calls = []
    mode = 'online'

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if mode == 'offline':
                raise URLError('offline')
            if mode == 'unauthorized':
                raise HTTPError(request.full_url, 401, 'Unauthorized', {}, None)
            return io.BytesIO(b'{"data":[{"id":"fixture-model"}]}')

    monkeypatch.setattr(analysis.urllib.request, 'build_opener', lambda *_: Opener())
    cloud = {'name': 'Fixture', 'provider_type': 'cloud', 'base_url': 'https://one.example/v1',
             'model': 'fixture-model', 'api_key': 'fixture-key'}
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        test = '/api/llm-configs/test'
        local = cloud | {'provider_type': 'local', 'base_url': 'http://localhost:1234/v1', 'api_key': ''}
        mode = 'offline'
        assert '无法连接' in client.post(test, json=local).json()['detail']
        mode = 'online'
        assert client.post(test, json=local | {'model': 'missing'}).json()['model_available'] is False
        assert client.post(test, json=local).json()['model_available'] is True
        saved = client.post('/api/llm-configs', json=cloud).json()
        assert saved['has_api_key'] and 'api_key' not in saved
        assert client.post(test, json=cloud).json()['model_available'] is True
        mode = 'unauthorized'
        assert 'API Key 无效' in client.post(test, json=cloud).json()['detail']
        assert len(calls) == 5
        assert all(req.get_method() == 'GET' and req.data is None and req.full_url.endswith('/models') for req in calls)
        moved = cloud | {'config_id': saved['id'], 'base_url': 'https://two.example/v1', 'api_key': ''}
        assert client.post(test, json=moved).status_code == 400
        assert client.put('/api/llm-configs/' + saved['id'], json=moved).status_code == 400
        assert len(calls) == 5


def test_cloud_analysis_and_inline_minutes_require_destination_confirmation(tmp_path, monkeypatch):
    import json
    app = create_app(tmp_path, start_worker=False)
    calls = []
    monkeypatch.setattr(analysis.OpenAICompatibleProvider, 'generate_ai_minutes',
                        lambda *args: calls.append(args))
    with TestClient(app) as client:
        store = app.state.store
        mid = client.post('/api/meetings', files={'file': ('test.wav', b'audio')}).json()['id']
        segment = {'id': 's1', 'start': 0, 'end': 2, 'speaker': 'A', 'text': 'Local fixture only'}
        assert client.put(f'/api/meetings/{mid}/transcript', json={'revision': 0, 'segments': [segment]}).is_success
        profile = {'name': 'Fixture', 'provider_type': 'cloud', 'base_url': 'https://one.example/v1',
                   'model': 'fixture-model', 'api_key': 'fixture-key'}
        pid = client.post('/api/llm-configs', json=profile).json()['id']
        destination = {key: profile[key] for key in ('base_url', 'provider_type', 'model')} | {'id': pid}
        for action in ('analysis-jobs', 'minutes/compose', 'minutes/ai'):
            url = f'/api/meetings/{mid}/{action}'
            assert client.post(url, json={'profile_id': pid}).status_code == 409
            assert client.post(url, json={'profile_id': pid, 'confirm_cloud': True,
                                          'expected_provider': destination | {'model': 'different'}}).status_code == 409
        assert not calls and not store.all('SELECT id FROM jobs')
        result = client.post(f'/api/meetings/{mid}/analysis-jobs', json={
            'profile_id': pid, 'confirm_cloud': True, 'expected_provider': destination})
        assert result.is_success, result.text
        job = json.loads(store.one('SELECT config FROM jobs')['config'])
        assert job['cloud_confirmed'] and job['provider']['base_url'] == destination['base_url']
