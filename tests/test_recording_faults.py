"""First fault-injection group: owns only temporary files and synthetic child processes."""
import hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Store
from app.recordings import RecordingStore
from app.native_capture import NativeCapture
from app.recording_health import RecordingHealth
from app.recording_runtime import RecordingRuntime
from app.lifecycle import Lifecycle


def native_fixture(root):
    store=Store(root);recordings=RecordingStore(store);journal=Lifecycle(root);recordings.lifecycle=journal
    native=NativeCapture(store,recordings);health=RecordingHealth(store,recordings,native)
    runtime=RecordingRuntime(store,recordings,native,health,journal)
    session=recordings.create('audio/wav');rid=session['id'];folder=recordings.folder(rid)
    session['source']='macos';recordings.write(folder/'session.json',session)
    recordings.write(folder/'capture.json',{'state':'recording','duration':1,'error':'','system_frames':16000})
    (folder/'system.pcm').write_bytes(b'\1\0'*16000)
    return store,recordings,journal,native,runtime,rid,folder


def test_killed_capture_is_observed_once_and_audio_remains_recoverable(tmp_path):
    store,recordings,journal,native,runtime,rid,folder=native_fixture(tmp_path)
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    native.processes[rid]=process;native.last_seen[rid]=time.monotonic()
    before=hashlib.sha256((folder/'system.pcm').read_bytes()).hexdigest()
    try:
        assert runtime.read(rid)['capture']['state']=='recording'
        process.kill();process.wait(timeout=3)
        native.stop_abandoned();native.status(rid);native.status(rid)
        assert runtime.read(rid)['phase']=='interrupted'
        assert len([e for e in journal.events(rid) if e['code']=='capture.exited'])==1
        assert json.loads((folder/'capture-exit.json').read_text())['exit_code']==-signal.SIGKILL
        assert hashlib.sha256((folder/'system.pcm').read_bytes()).hexdigest()==before
        assert RecordingHealth(store,recordings,native).snapshot(rid)['audio']['recoverable']
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=3)


def test_stale_heartbeat_is_unknown_then_recovers_without_restarting_capture(tmp_path):
    _,_,journal,native,runtime,rid,folder=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    path=folder/'capture.json';os.utime(path,(time.time()-30,time.time()-30))
    runtime.observe();assert runtime.read(rid)['phase']=='unknown'
    assert runtime.read(rid)['storage']['saved_bytes']==32000
    process.terminate.assert_not_called();process.kill.assert_not_called()
    os.utime(path,None);runtime.observe();assert runtime.read(rid)['phase']=='recording'
    codes=[e['code'] for e in journal.events(rid)]
    assert 'capture.unknown' in codes and 'capture.recording' in codes


def test_abrupt_service_exit_is_distinct_from_clean_shutdown_and_does_not_restart_capture(tmp_path):
    script='''import os,sys,json
from pathlib import Path
from app.lifecycle import Lifecycle
from app.db import Store
from app.recordings import RecordingStore
root=Path(sys.argv[1]);store=Store(root);journal=Lifecycle(root);journal.start()
r=RecordingStore(store);r.lifecycle=journal;s=r.create('audio/wav');s['source']='macos'
r.write(r.folder(s['id'])/'session.json',s)
r.write(r.folder(s['id'])/'capture.json',{'state':'recording','duration':1})
with (r.folder(s['id'])/'system.pcm').open('wb') as f:f.write(b'preserved-audio');f.flush();os.fsync(f.fileno())
print(s['id'],flush=True)
os._exit(23)
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=8)
    assert result.returncode==23,result.stderr
    rid=result.stdout.strip()
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        events=client.get('/api/runtime/lifecycle').json()['events']
        assert any(e['code']=='service.previous_unclosed' for e in events)
        report=client.get(f'/api/recordings/{rid}/health').json()
        assert report['runtime']['phase']=='interrupted' and report['audio']['recoverable']
        assert client.get('/api/live-sessions').json()==[]
        assert (tmp_path/'recordings'/rid/'system.pcm').read_bytes()==b'preserved-audio'
    journal=Lifecycle(tmp_path)
    assert journal.events()[0]['code']=='service.clean_shutdown'


def test_disk_failure_does_not_hide_capture_and_retry_clears_storage_error(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id']
        original=RecordingStore.chunk
        def fail(*_):raise OSError('private-path-and-key')
        monkeypatch.setattr(RecordingStore,'chunk',fail)
        response=client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'audio')})
        assert response.status_code==503 and 'private-path' not in response.text
        state=client.get(f'/api/recordings/{rid}/health').json()['runtime']
        assert state['capture']['state']=='recording' and state['storage']['state']=='failed'
        monkeypatch.setattr(RecordingStore,'chunk',original)
        assert client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'audio')}).status_code==200
        assert client.get(f'/api/recordings/{rid}/health').json()['runtime']['storage']['state']=='writing'


def test_asr_failure_never_changes_capture_or_file_state(tmp_path):
    store,recordings,journal,native,runtime,rid,_=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    session=recordings.read(rid);session['draft_engine']='qwen';recordings.write(recordings.folder(rid)/'session.json',session)
    from app.live_results import LiveResults
    cache=LiveResults(store,recordings);attempt,_=cache.begin(rid,'native','qwen',0,1,b'fake',{})
    cache.fail(attempt,'secret-model-trace');cache.fail(attempt,'repeated');runtime.observe();state=runtime.read(rid)
    assert state['asr']['state']=='failed' and state['capture']['state']=='recording' and state['storage']['state']=='writing'
    assert 'secret-model-trace' not in json.dumps(state)
    assert len([e for e in journal.events(rid) if e['code']=='asr.failed'])==1
    assert journal.checkpoints(rid)['storage']['metrics']['saved_bytes']==32000


def test_browser_state_transitions_are_ordered_and_stopped_capture_cannot_resume(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id'];url=f'/api/recordings/{rid}/capture-state'
        for state,phase in [('recording','recording'),('paused','paused'),('recording','recording'),('stopped','saving')]:
            assert client.post(url,json={'state':state}).status_code==200
            assert client.get(f'/api/recordings/{rid}/health').json()['runtime']['phase']==phase
        assert client.post(url,json={'state':'recording'}).status_code==409

def test_browser_disconnect_and_recorder_error_do_not_claim_healthy_capture(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id']
        recordings=RecordingStore(client.app.state.store)
        value=recordings.read(rid);value['updated']=time.time()-120
        recordings.write(recordings.folder(rid)/'session.json',value)
        assert client.get(f'/api/recordings/{rid}/health').json()['runtime']['phase']=='unknown'
        url=f'/api/recordings/{rid}/capture-state'
        assert client.post(url,json={'state':'interrupted'}).status_code==200
        assert client.get(f'/api/recordings/{rid}/health').json()['runtime']['phase']=='interrupted'
        assert client.post(url,json={'state':'recording'}).status_code==409


def test_lifecycle_lock_and_allowlist_and_record_failure_are_independent_of_audio(tmp_path,monkeypatch):
    journal=Lifecycle(tmp_path);journal.start()
    try:
        second=Lifecycle(tmp_path)
        with pytest.raises(RuntimeError,match='第二个'):second.start()
        assert journal.record('service.started',pid=123,raw_text='secret',api_key='key')
        assert 'secret' not in json.dumps(journal.events()) and 'key' not in json.dumps(journal.events())
        def fail():raise __import__('sqlite3').OperationalError('full')
        monkeypatch.setattr(journal,'connect',fail)
        assert journal.record('service.started') is False
        store=Store(tmp_path/'audio');r=RecordingStore(store);r.lifecycle=journal
        rid=r.create('audio/webm')['id'];r.chunk(rid,0,b'still-saved')
        assert (r.folder(rid)/'00000000.part').read_bytes()==b'still-saved'
    finally:journal.close()

def test_reading_status_and_diagnostics_does_not_create_events_or_change_audio(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        client.app.state.lifecycle.observer=None
        rid=client.post('/api/recordings',json={}).json()['id']
        client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'saved-audio')})
        before=client.app.state.lifecycle.events(rid)
        for route in ('health','diagnostics','health'):
            assert client.get(f'/api/recordings/{rid}/{route}').status_code==200
        assert client.app.state.lifecycle.events(rid)==before
        assert (tmp_path/'recordings'/rid/'00000000.part').read_bytes()==b'saved-audio'
