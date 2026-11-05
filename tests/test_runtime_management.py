import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid

def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    monkeypatch.setattr('app.runtime_management.engine_catalog',lambda:[{'id':'whisper','label':'合成 Whisper','ready':True,'model':'synthetic'}])
    monkeypatch.setattr('app.main.NativeCapture.capability',lambda _:{'supported':True,'available':True,'screen_permission':True,'microphone_permission':False})
    def forbidden(*args,**kwargs):raise AssertionError('runtime overview must not contact or load a model')
    monkeypatch.setattr('app.analysis.OpenAICompatibleProvider.status',forbidden)
    return app

def test_runtime_inventory_is_passive_and_excludes_secrets_and_payloads(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch);store=app.state.store
    store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,?,?,?,?)',(uid(),'测试配置','cloud','https://example.com/v1','test-model','secret-key-do-not-return'))
    store.execute("INSERT INTO jobs(id,state,kind,stage,progress,config,error) VALUES('job1','queued','analysis','排队',.5,'private-transcript','private-error')")
    with TestClient(app) as client:
        store.execute("INSERT INTO live_sessions(recording_id,source,config,cursor) VALUES('session1','native','{\"engine\":\"whisper\"}',240000)")
        response=client.get('/api/runtime/overview');assert response.status_code==200
        data=response.json()
        assert data['passive'] and not data['llm']['connection_checked']
        assert data['llm']['profiles'][0]['name']=='测试配置'
        assert data['sessions'][0]['engine']=='whisper' and data['sessions'][0]['confirmed_until']==15
        assert data['job_counts']['queued']==1 and data['jobs'][0]['progress']==.5
        assert not data['qwen']['loaded'] and not data['capture_test_active']
        for secret in ('secret-key-do-not-return','private-transcript','private-error','base_url'):assert secret not in response.text
        assert not app.state.live_sessions.tasks

def test_runtime_inventory_degrades_individual_checks_without_losing_page(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    def failed():raise OSError('private OS error')
    monkeypatch.setattr('app.runtime_management.engine_catalog',failed)
    monkeypatch.setattr('app.runtime_management.shutil.disk_usage',lambda _:failed())
    with TestClient(app) as client:
        data=client.get('/api/runtime/overview').json()
        assert data['engines']==[] and data['storage']['free_bytes'] is None
        assert len(data['issues'])==2 and 'private OS error' not in json.dumps(data)
        assert data['native']['available']
