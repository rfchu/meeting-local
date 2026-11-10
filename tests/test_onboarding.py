import io,json,time,wave,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.capture_probe import CaptureProbe
from app.native_capture import NativeCapture
from app.db import Store

class FakeProcess:
    def __init__(self,args,**kwargs):
        self.code=None;self.stdin=io.BytesIO();self.args=args
        self.folder=Path(args[args.index('--output')+1])
        (self.folder/'capture.json').write_text(json.dumps({'state':'recording','duration':0}))
        (self.folder/'system.pcm').write_bytes(b'\x01\x00'*16000*12)
        if '--microphone' in args:(self.folder/'microphone.pcm').write_bytes(b'\x02\x00'*16000*12)
    def poll(self):return self.code
    def wait(self,timeout=None):self.code=0;return 0
    def terminate(self):self.code=-15
    def kill(self):self.code=-9

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    native=app.state.capture_probes.native
    monkeypatch.setattr(native,'available',lambda:True)
    monkeypatch.setattr(native,'capability',lambda:{'available':True,'supported':True,'screen_permission':True,'microphone_permission':True,'build_command':'test'})
    monkeypatch.setattr(app.state.capture_probes,'start_monitor',lambda:None)
    monkeypatch.setattr('app.capture_probe.subprocess.Popen',FakeProcess)
    monkeypatch.setattr('app.onboarding.engine_catalog',lambda:[{'id':'qwen','label':'Qwen','ready':False}])
    with TestClient(app) as client:yield client,app

def test_readiness_is_read_only_and_never_reports_llm_configuration_as_verified(fixture):
    client,app=fixture;store=app.state.store
    store.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES('test','测试','local','http://localhost:1234/v1','qwen','never-return-this')")
    result=client.get('/api/recording-readiness')
    assert result.status_code==200
    value=result.json();assert value['llm']['configured'] and not value['llm']['verified']
    assert value['engines'][0]['ready'] is False and value['storage']['writable']
    assert 'api_key' not in result.text and 'never-return-this' not in result.text
    assert not store.one('SELECT id FROM meetings') and not store.one('SELECT id FROM jobs')

def test_sample_and_exports_are_stateless_and_include_real_source_ids(fixture):
    client,app=fixture;store=app.state.store
    sample=client.get('/api/onboarding/sample').json()
    ids={s['id'] for s in sample['segments']};assert len(ids)==3
    assert '虚构' in sample['title'] and '未确定' in sample['note']['text']
    for field in ('decisions','action_items','open_questions'):
        for item in sample['minutes'][field]:assert set(item['source_segment_ids'])<=ids
    md=client.get('/api/onboarding/sample/export?format=md');assert md.status_code==200 and '虚构' in md.text
    docx=client.get('/api/onboarding/sample/export?format=docx')
    assert docx.status_code==200
    with zipfile.ZipFile(io.BytesIO(docx.content)) as archive:assert '虚构' in archive.read('word/document.xml').decode()
    pdf=client.get('/api/onboarding/sample/export?format=pdf');assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    assert client.get('/api/onboarding/sample/export?format=exe').status_code==422
    assert not store.one('SELECT id FROM meetings') and not store.one('SELECT id FROM artifacts') and not store.one('SELECT id FROM jobs')

def test_native_probe_does_not_create_recordings_and_caps_mixed_audio_at_ten_seconds(fixture):
    client,app=fixture;probes=app.state.capture_probes
    result=client.post('/api/capture-tests',json={'source':'macos-both'})
    assert result.status_code==201;key=result.json()['id'];item=probes.items[key]
    assert '--microphone' in item['process'].args
    assert not (app.state.store.root/'recordings'/key).exists()
    assert not app.state.store.one('SELECT id FROM meetings')
    assert client.get(f'/api/capture-tests/{key}/audio').status_code==409
    path = item['folder']/'capture.json'
    path.write_text(json.dumps({'state':'recording','duration':2,'system_levels':[.04],'microphone_levels':[.08]}))
    assert client.get(f'/api/capture-tests/{key}').json()['levels'] == {'system':.04,'microphone':.08}
    path.write_text(json.dumps({'state':'recording','duration':11.2,'system_levels':[0],'microphone_levels':[0]}))
    status=client.get(f'/api/capture-tests/{key}').json();assert status['state']=='completed' and status['duration']==10
    assert status['levels'] == {'system':.04,'microphone':.08}
    audio=client.get(f'/api/capture-tests/{key}/audio');assert audio.status_code==200
    assert audio.headers['cache-control']=='no-store'
    with wave.open(io.BytesIO(audio.content),'rb') as wav:
        assert wav.getframerate()==16000 and wav.getnchannels()==1 and wav.getsampwidth()==2 and wav.getnframes()==160000
        assert wav.readframes(1)==b'\x03\x00'
    folder=item['folder'];assert folder.exists()
    assert client.delete(f'/api/capture-tests/{key}').status_code==200
    assert not folder.exists() and item['process'].poll() is not None
    assert client.get(f'/api/capture-tests/{key}').status_code==404
    assert client.delete(f'/api/capture-tests/{key}').status_code==200

def test_capture_probe_and_formal_native_capture_are_mutually_exclusive(fixture):
    client,app=fixture;probes=app.state.capture_probes
    key=client.post('/api/capture-tests',json={'source':'macos-system'}).json()['id']
    assert client.post('/api/capture-tests',json={'source':'macos-system'}).status_code==409
    assert client.post('/api/native-capture',json={'microphone':False}).status_code==409
    client.delete(f'/api/capture-tests/{key}')
    probes.native.processes['other']=SimpleNamespace(poll=lambda:None)
    assert client.post('/api/capture-tests',json={'source':'macos-system'}).status_code==409
    probes.native.processes.clear()
    assert client.post('/api/capture-tests',json={'source':'browser'}).status_code==422

def test_probe_permission_failure_timeout_cleanup_and_expiry(fixture):
    client,app=fixture;probes=app.state.capture_probes
    key=client.post('/api/capture-tests',json={'source':'macos-system'}).json()['id'];item=probes.items[key]
    (item['folder']/'capture.json').write_text(json.dumps({'state':'failed','error':'权限未允许'}))
    result=client.get(f'/api/capture-tests/{key}').json();assert result['state']=='failed' and '权限' in result['error']
    assert item['process'].poll() is not None
    item['finished']=time.monotonic()-301;folder=item['folder'];probes.prune();assert not folder.exists()
    key=client.post('/api/capture-tests',json={'source':'macos-system'}).json()['id'];item=probes.items[key]
    item['created']=time.monotonic()-46
    assert client.get(f'/api/capture-tests/{key}').json()['state']=='failed'
    assert item['process'].poll() is not None
    folder=item['folder'];probes.close();assert not folder.exists() and not probes.items

def test_sample_does_not_change_user_preparation(fixture):
    client,app=fixture;store=app.state.store
    store.execute("INSERT INTO meeting_preparation(id,payload) VALUES(1,?)",(json.dumps({'title':'保留的准备'}),))
    client.get('/api/onboarding/sample');client.get('/api/onboarding/sample/export?format=md')
    assert json.loads(store.one('SELECT payload FROM meeting_preparation')['payload'])['title']=='保留的准备'
