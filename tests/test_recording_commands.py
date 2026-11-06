"""Temporary metadata and synthetic capture only; never opens audio devices."""
import json,uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.recording_commands import RecordingCommands,CommandError,control_view
from tests.test_recording_faults import native_fixture


def fixture(root):
    store,recordings,_,native,_,rid,folder=native_fixture(root)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    calls=[];boundaries=[]
    def command(key,kind):
        calls.append(kind)
        status=json.loads((folder/'capture.json').read_text());status['state']={'pause':'paused','resume':'recording','stop':'stopped'}[kind]
        recordings.write(folder/'capture.json',status)
        if kind=='stop':process.poll.return_value=0
        return native.status(key,touch=False)
    native.command=command
    service=RecordingCommands(recordings,native,lambda *args:boundaries.append(args))
    return service,recordings,native,rid,folder,calls,boundaries


def ident():return uuid.uuid4().hex


def test_idempotent_native_commands_and_replay_after_intervening_resume(tmp_path):
    service,recordings,_,rid,folder,calls,boundaries=fixture(tmp_path);key=ident();audio=(folder/'system.pcm').read_bytes()
    first=service.execute(rid,'pause',request_id=key,expected_version=0)
    assert first['control']['version']==1 and first['receipt']['outcome']=='applied'
    again=service.execute(rid,'pause',request_id=key,expected_version=0)
    assert again['receipt']['replayed'] and calls==['pause']
    service.execute(rid,'pause',request_id=ident(),expected_version=1)
    service.execute(rid,'resume',request_id=ident(),expected_version=2)
    old=service.execute(rid,'pause',request_id=key,expected_version=0)
    assert old['state']=='recording' and old['receipt']['state']=='paused'
    assert old['receipt']['replayed'] and calls==['pause','resume']
    assert len(boundaries)>=1 and audio==(folder/'system.pcm').read_bytes()
    assert control_view(recordings.read(rid))['version']==3


def test_stale_version_and_reused_key_do_not_send_commands(tmp_path):
    service,_,_,rid,_,calls,_=fixture(tmp_path);key=ident()
    service.execute(rid,'pause',request_id=key,expected_version=0)
    with pytest.raises(CommandError) as error:service.execute(rid,'resume',request_id=ident(),expected_version=0)
    assert error.value.code=='version_conflict'
    with pytest.raises(CommandError) as error:service.execute(rid,'resume',request_id=key,expected_version=1)
    assert error.value.code=='request_reused' and calls==['pause']


def test_concurrent_commands_with_one_version_cannot_both_apply(tmp_path):
    service,recordings,_,rid,_,calls,_=fixture(tmp_path)
    def run(command):
        try:return service.execute(rid,command,request_id=ident(),expected_version=0)
        except CommandError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,['pause','resume']))
    assert sum(isinstance(v,dict) for v in results)==1 and 'version_conflict' in results
    assert control_view(recordings.read(rid))['version']==1 and len(calls)<=1


def test_intent_write_failure_prevents_physical_control(tmp_path,monkeypatch):
    service,recordings,_,rid,_,calls,_=fixture(tmp_path)
    def fail(*_):raise OSError('private-path')
    monkeypatch.setattr(recordings,'write',fail)
    with pytest.raises(OSError):service.execute(rid,'pause',request_id=ident(),expected_version=0)
    assert calls==[] and control_view(recordings.read(rid))['version']==0


def test_receipt_write_failure_reconciles_after_restart_without_resending(tmp_path,monkeypatch):
    service,recordings,native,rid,_,calls,_=fixture(tmp_path);write=recordings.write;key=ident()
    def fail_receipt(path,value):
        if 'control' in value and value['control']['pending'] is None:raise OSError('private-path')
        return write(path,value)
    monkeypatch.setattr(recordings,'write',fail_receipt)
    with pytest.raises(CommandError) as error:service.execute(rid,'pause',request_id=key,expected_version=0)
    assert error.value.code=='command_unconfirmed' and error.value.control['status']=='unconfirmed'
    monkeypatch.setattr(recordings,'write',write)
    restored=RecordingCommands(recordings,native)
    result=restored.execute(rid,'pause',request_id=key,expected_version=0)
    assert result['receipt']['outcome']=='confirmed_after_retry' and calls==['pause']
    assert result['control']['status']=='idle'


def test_uncertain_command_is_never_blindly_resent_and_stop_can_supersede(tmp_path):
    service,recordings,native,rid,_,calls,_=fixture(tmp_path);original=native.command;key=ident()
    def unknown(*args):calls.append('uncertain_pause');raise RuntimeError('private failure')
    native.command=unknown
    with pytest.raises(CommandError):service.execute(rid,'pause',request_id=key,expected_version=0)
    for command,request,version in [('pause',key,0),('resume',ident(),1)]:
        with pytest.raises(CommandError) as error:service.execute(rid,command,request_id=request,expected_version=version)
        assert error.value.code=='command_unconfirmed'
    assert calls==['uncertain_pause']
    native.command=original
    result=service.execute(rid,'stop',request_id=ident(),expected_version=1)
    assert result['state']=='stopped' and result['control']['status']=='idle'
    with pytest.raises(CommandError) as error:service.execute(rid,'pause',request_id=key,expected_version=0)
    assert error.value.code=='command_superseded' and calls==['uncertain_pause','stop']
    assert control_view(recordings.read(rid))['version']==2


@pytest.mark.parametrize('command',['pause','resume'])
def test_terminal_capture_cannot_restart_after_service_reconstruction(tmp_path,command):
    service,recordings,native,rid,_,calls,_=fixture(tmp_path)
    service.execute(rid,'stop',request_id=ident(),expected_version=0)
    service=RecordingCommands(recordings,native)
    with pytest.raises(CommandError) as error:service.execute(rid,command,request_id=ident(),expected_version=1)
    assert error.value.code=='terminal_state' and calls==['stop']
    service.execute(rid,'stop',request_id=ident(),expected_version=1)
    assert calls==['stop']


def test_bounded_receipts_do_not_make_evicted_old_requests_executable(tmp_path):
    service,recordings,_,rid,_,calls,_=fixture(tmp_path);old=ident()
    service.execute(rid,'pause',request_id=old,expected_version=0)
    for version in range(1,67):service.execute(rid,'pause',request_id=ident(),expected_version=version)
    assert len(recordings.read(rid)['control']['receipts'])==64
    with pytest.raises(CommandError) as error:service.execute(rid,'pause',request_id=old,expected_version=0)
    assert error.value.code=='version_conflict' and calls==['pause']


def test_http_browser_reporting_legacy_adapter_and_native_ownership(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id'];url=f'/api/recordings/{rid}/commands'
        assert client.get(f'/api/recordings/{rid}/health').json()['runtime']['control']['version']==0
        def send(command,version,state=None,key=None):
            return client.post(url,json={'command':command,'request_id':key or ident(),'expected_version':version,'observed_state':state})
        assert send('pause',0).json()['detail']['code']=='observation_required'
        key=ident();first=send('pause',0,'paused',key);assert first.status_code==200
        assert first.json()['native'] is None and first.json()['state']=='paused'
        assert send('pause',0,'paused',key).json()['receipt']['replayed']
        assert send('resume',0,'recording').json()['detail']['code']=='version_conflict'
        assert client.post(f'/api/recordings/{rid}/capture-state',json={'state':'recording'}).status_code==200
        assert client.post(f'/api/native-capture/{rid}/command',json={'command':'pause'}).status_code==409
        assert send('stop',2,'stopped').status_code==200
        assert send('resume',3,'recording').json()['detail']['code']=='terminal_state'
        assert client.post(url,json={'command':'pause','request_id':'bad','expected_version':0}).status_code==422
        assert send('stop',3,'stopped').status_code==200


def test_browser_save_failure_keeps_chunks_and_retry_publishes_once(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id'];finish=f'/api/recordings/{rid}/finish'
        assert client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'audio')}).status_code==200
        assert client.post(finish,json={'chunk_count':2}).status_code==409
        assert (tmp_path/'recordings'/rid/'00000000.part').read_bytes()==b'audio'
        for _ in range(2):assert client.post(finish,json={'chunk_count':1}).status_code==200
        assert len(client.app.state.store.all('SELECT id FROM meetings WHERE id=?',(rid,)))==1
        assert client.get(f'/api/recordings/{rid}/health').json()['state']=='completed'


def test_observation_and_metadata_updates_do_not_advance_command_version(tmp_path):
    service,recordings,native,rid,folder,_,_=fixture(tmp_path)
    service.execute(rid,'pause',request_id=ident(),expected_version=0)
    recordings.metadata(rid,[],12,0)
    assert control_view(recordings.read(rid))['version']==1
    # A2 reads expose no request identifiers or command histories.
    view=control_view(recordings.read(rid))
    assert set(view)=={'version','status','pending_command'}
    assert len(recordings.read(rid)['control']['receipts'])==1


def test_native_cannot_be_controlled_through_browser_report_or_wrong_legacy_owner(tmp_path):
    service,_,_,rid,_,calls,_=fixture(tmp_path)
    with pytest.raises(CommandError):service.execute(rid,'pause',observed_state='paused',request_id=ident(),expected_version=0)
    with pytest.raises(CommandError):service.execute(rid,'pause',source='browser',request_id=ident(),expected_version=0)
    assert calls==[]
