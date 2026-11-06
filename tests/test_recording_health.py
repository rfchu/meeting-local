import json
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Store
from app.recordings import RecordingStore
from app.recording_health import RecordingHealth
from app.live_results import LiveResults


@pytest.fixture
def inventory(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store)
    native=Mock();native.is_running.return_value=False
    return store,recordings,native,RecordingHealth(store,recordings,native)


def test_missing_and_zero_length_chunks_block_completion_and_restart_preserves_tail(inventory):
    store,recordings,_,health=inventory
    session=recordings.create('audio/webm');rid=session['id']
    recordings.metadata(rid,[{'captured_at':1,'raw_text':'tail note','note_type':'note'}],12,3)
    recordings.chunk(rid,0,b'head');recordings.chunk(rid,2,b'tail')
    snapshot=health.snapshot(rid)
    assert snapshot['audio']['missing_chunk_indices']==[1]
    assert snapshot['audio']['contiguous_chunks']==1
    assert not snapshot['audio']['recoverable']
    with pytest.raises(ValueError):recordings.finish(rid,3)
    recordings=RecordingStore(Store(store.root))
    recordings.chunk(rid,1,b'middle')
    (recordings.folder(rid)/'00000001.part').write_bytes(b'')
    with pytest.raises(ValueError,match='为空'):recordings.finish(rid,3)
    (recordings.folder(rid)/'00000001.part').unlink()
    recordings.chunk(rid,1,b'middle')
    assert health.snapshot(rid)['audio']['recoverable']
    recordings.finish(rid,3);recordings.finish(rid,3)
    assert (store.directory(rid)/'original').read_bytes()==b'headmiddletail'
    assert len(store.all('SELECT * FROM meeting_notes WHERE meeting_id=?',(rid,)))==1
    result=health.snapshot(rid)
    assert result['state']=='completed' and result['audio']['saved_bytes']==14
    assert result['audio']['missing_chunk_count']==0 and not result['audio']['recoverable']


def test_diagnostics_allowlist_excludes_content_keys_paths_identifiers_and_errors(inventory):
    store,recordings,_,health=inventory
    session=recordings.create('audio/webm');rid=session['id'];secret='PRIVATE-SENTINEL-secret-key'
    session.update(duration=30,notes=[{'raw_text':secret}],error=secret,base_url='https://'+secret,model=secret)
    recordings.write(recordings.folder(rid)/'session.json',session)
    live=LiveResults(store,recordings)
    first,_=live.begin(rid,'browser','qwen',0,15,b'a',{'path':str(store.root),'key':secret})
    live.complete(first,[{'text':secret,'speaker':secret}])
    second,_=live.begin(rid,'browser','qwen',15,15,b'b',{'model':secret})
    live.fail(second,secret)
    report=health.snapshot(rid);payload=json.dumps(report)
    assert secret not in payload and str(store.root) not in payload and rid not in payload
    assert report['notes_count']==1
    assert report['asr']['qwen']=={'covered_seconds':15,'failed_attempts':1,'running_attempts':0}
    assert live.overview(rid,'qwen')['recent_failures'][0]['error']==secret


def test_native_inspection_does_not_renew_capture_heartbeat(inventory):
    store,recordings,native,health=inventory
    session=recordings.create('audio/wav');rid=session['id']
    session['source']='macos';recordings.write(recordings.folder(rid)/'session.json',session)
    recordings.write(recordings.folder(rid)/'capture.json',{'state':'recording','duration':12,'error':'private-error'})
    (recordings.folder(rid)/'system.pcm').write_bytes(b'\0\0'*16000)
    native.is_running.return_value=True
    assert health.snapshot(rid)['native_running']
    assert not health.snapshot(rid)['audio']['recoverable']
    native.status.assert_not_called()
    native.is_running.return_value=False
    report=health.snapshot(rid)
    assert report['state']=='interrupted' and report['audio']['recoverable']
    assert report['audio']['tracks'][0]['saved_seconds']==1
    assert 'private-error' not in json.dumps(report)

def test_stale_capture_and_exit_code_are_reported_without_touching_audio(inventory):
    import os,time
    store,recordings,native,health=inventory
    session=recordings.create('audio/wav');rid=session['id'];session['source']='macos'
    recordings.write(recordings.folder(rid)/'session.json',session)
    folder=recordings.folder(rid);path=folder/'capture.json'
    recordings.write(path,{'state':'recording','duration':649})
    (folder/'system.pcm').write_bytes(b'saved-audio')
    recordings.write(folder/'capture-exit.json',{'exit_code':-9,'private_error':'secret'})
    os.utime(path,(time.time()-180,time.time()-180));before=path.read_bytes()
    report=health.snapshot(rid)
    assert report['capture']['heartbeat_stale'] and report['capture']['heartbeat_age_seconds']>=180
    assert report['capture']['exit_code']==-9 and 'capture_heartbeat_stale' in report['warning_codes']
    assert report['audio']['recoverable'] and 'secret' not in json.dumps(report)
    assert path.read_bytes()==before and (folder/'system.pcm').read_bytes()==b'saved-audio'


def test_preview_overlap_and_failures_do_not_inflate_confirmed_coverage(inventory):
    store,recordings,_,health=inventory
    session=recordings.create('audio/webm');rid=session['id'];recordings.metadata(rid,[],20,0)
    live=LiveResults(store,recordings)
    for offset,duration,state in [(0,15,'completed'),(5,15,'completed'),(0,5,'preview'),(0,20,'failed')]:
        aid,_=live.begin(rid,'browser','qwen',offset,duration,bytes([int(offset+duration)]),{})
        if state=='completed':live.complete(aid,[])
        elif state=='preview':live.preview(aid)
        else:live.fail(aid,'error')
    assert health.snapshot(rid)['asr']['qwen']['covered_seconds']==20


def test_api_diagnostics_download_and_inventory_are_read_only(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id']
        client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'audio')})
        before=(tmp_path/'recordings'/rid/'session.json').read_bytes()
        response=client.get(f'/api/recordings/{rid}/diagnostics')
        assert response.status_code==200
        assert 'attachment' in response.headers['content-disposition']
        assert response.headers['cache-control']=='no-store'
        assert client.get('/api/recordings').json()[0]['health']['audio']==response.json()['audio']
        assert client.get(f'/api/recordings/{rid}/health').json()['audio']['saved_chunks']==1
        assert (tmp_path/'recordings'/rid/'session.json').read_bytes()==before
        assert client.get('/api/recordings/'+'0'*32+'/health').status_code==404


def test_failed_empty_native_start_is_not_recoverable_and_reason_is_allowlisted(inventory):
    store,recordings,native,health=inventory
    session=recordings.create('audio/wav');rid=session['id'];session['source']='macos'
    recordings.write(recordings.folder(rid)/'session.json',session)
    recordings.write(recordings.folder(rid)/'capture.json',{'state':'failed','duration':0,'error':'请在系统设置 → 隐私与安全性 中允许听记采集器，PRIVATE-SENTINEL'})
    before=(recordings.folder(rid)/'session.json').read_bytes()
    report=health.snapshot(rid)
    assert report['empty_session'] and not report['audio']['recoverable']
    assert report['capture_failure_code']=='permission_required'
    assert 'PRIVATE-SENTINEL' not in json.dumps(report)
    assert (recordings.folder(rid)/'session.json').read_bytes()==before
    native.is_running.return_value=True
    assert not health.snapshot(rid)['empty_session']


@pytest.mark.parametrize('material',['pcm','note','brief','asr','temporary'])
def test_empty_classification_preserves_any_saved_material(inventory,material):
    store,recordings,_,health=inventory
    session=recordings.create('audio/wav');rid=session['id'];folder=recordings.folder(rid)
    session['source']='macos'
    if material=='note':session['notes']=[{'raw_text':'保留的笔记'}]
    if material=='brief':session['brief']={'purpose':'保留的准备'}
    recordings.write(folder/'session.json',session)
    recordings.write(folder/'capture.json',{'state':'failed','duration':0})
    if material=='pcm':(folder/'system.pcm').write_bytes(b'\0\0')
    if material=='temporary':(folder/'audio.tmp').write_bytes(b'tail')
    if material=='asr':LiveResults(store,recordings).begin(rid,'native','qwen',0,1,b'x',{})
    assert not health.snapshot(rid)['empty_session']


def test_empty_archive_is_reversible_and_cannot_hide_material(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        app=client.app;recordings=RecordingStore(app.state.store)
        session=recordings.create('audio/wav');rid=session['id'];folder=recordings.folder(rid)
        session['source']='macos';recordings.write(folder/'session.json',session)
        recordings.write(folder/'capture.json',{'state':'failed','duration':0})
        endpoint=f'/api/recordings/{rid}/recovery-archive'
        assert client.post(endpoint).status_code==200
        assert client.get('/api/recordings').json()[0]['health']['recovery_archived']
        assert client.post(endpoint+'?archived=false').status_code==200
        assert not client.get('/api/recordings').json()[0]['health']['recovery_archived']
        (folder/'system.pcm').write_bytes(b'\0\0')
        assert client.post(endpoint).status_code==409
        assert (folder/'system.pcm').read_bytes()==b'\0\0'
        assert client.post('/api/recordings/'+'0'*32+'/recovery-archive').status_code==404
