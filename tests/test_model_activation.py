from app import model_activation, model_import, paths
from app.model_pack import parse_manifest
from test_model_pack import fixture_manifest
from app.main import create_app
from app.host_bridge import workspace_binding, workspace_mac
from fastapi.testclient import TestClient

from test_model_import import URL, complete, start


def test_activation_is_deferred_and_falls_back(tmp_path, monkeypatch):
    data, source = tmp_path / 'data', tmp_path / 'source'
    (source / 'models/test').mkdir(parents=True)
    (source / 'models/test/model.bin').write_bytes(b'good')
    monkeypatch.setattr(model_import, 'load_candidate', lambda _: parse_manifest(fixture_manifest(), '0.4.0'))
    monkeypatch.setenv('MEETING_MODELS', str(tmp_path / 'original'))
    monkeypatch.setenv('TINGJI_PORTABLE_SERVICE', '1')
    monkeypatch.setattr(paths, '_activated_models', None)
    key = data.parent / 'workspace.key'
    key.write_bytes(bytes(range(32)))
    key.chmod(0o600)

    def client_for(app):
        token = workspace_mac(key.read_bytes(), 'request:' + workspace_binding(app.state.paths))
        return TestClient(app, headers={'X-Tingji-Authorization': token})

    app = create_app(data, start_worker=False)
    with client_for(app) as client:
        start(client, source)
        assert complete(client)['can_activate']
        assert client.post(URL + '/activate').json()['activation_pending']
        assert paths.model_root() == paths.configured_model_root()

    with monkeypatch.context() as busy:
        from app.native_capture import NativeCapture
        busy.setattr(NativeCapture, 'ownership', lambda self: {'lease': 'held'})
        app = create_app(data, start_worker=False)
        with client_for(app) as client:
            assert client.get(URL).json()['activation_pending']
            assert paths.model_root() == paths.configured_model_root()

    with monkeypatch.context() as failed:
        def refuse(*args):
            raise OSError('injected write failure')
        failed.setattr(model_activation, 'atomic_json', refuse)
        with client_for(create_app(data, start_worker=False)) as client:
            assert client.get(URL).json()['activation_error'] == 'receipt_write_failed'
        assert paths.model_root() == paths.configured_model_root()

    with client_for(create_app(data, start_worker=False)) as client:
        assert client.get(URL).json()['active_version']
        selected = paths.model_root()
        assert selected != paths.configured_model_root()
        assert (selected / 'test/model.bin').read_bytes() == b'good'
    (selected / 'test/model.bin').write_bytes(b'bad!')
    with client_for(create_app(data, start_worker=False)) as client:
        assert client.get(URL).json()['activation_error'] == 'verification_failed'
    assert paths.model_root() == paths.configured_model_root()
    with client_for(create_app(data, start_worker=False)) as client:
        start(client, source)
        assert complete(client)['can_activate']
        assert client.post(URL + '/activate').json()['activation_pending']
    with client_for(create_app(data, start_worker=False)) as client:
        assert client.get(URL).json()['active_version']
        assert (paths.model_root() / 'test/model.bin').read_bytes() == b'good'
