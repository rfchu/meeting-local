"""Cross-page state projections never execute capture or model commands."""
import json,time,uuid
from unittest.mock import Mock
from fastapi.testclient import TestClient
from app.main import create_app


def native_session(app,created=None):
    runtime=app.state.recording_runtime;recordings=runtime.recordings;native=runtime.native
    session=recordings.create('audio/wav');rid=session['id'];session.update(source='macos',notes=[{'raw_text':'PRIVATE-CONTENT'}])
    if created is not None:session['created']=created
    folder=recordings.folder(rid);recordings.write(folder/'session.json',session)
    recordings.write(folder/'capture.json',{'state':'recording','duration':1,'system_frames':16000})
    (folder/'system.pcm').write_bytes(b'\1\0'*16000)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process;native.last_seen[rid]=123
    return rid,folder,process


def test_health_recovery_and_management_share_unconfirmed_control_without_clearing_it(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        app=client.app;native=app.state.recording_runtime.native
        rid,folder,process=native_session(app)
        def unknown(*_):raise RuntimeError('PRIVATE-DEVICE-ERROR')
        monkeypatch.setattr(native,'command',unknown)
        body={'command':'pause','request_id':uuid.uuid4().hex,'expected_version':0}
        try:
            assert client.post(f'/api/recordings/{rid}/commands',json=body).status_code==409
            metadata=(folder/'session.json').read_bytes();audio=(folder/'system.pcm').read_bytes()
            health=client.get(f'/api/recordings/{rid}/health').json()['runtime']
            recovery=next(s for s in client.get('/api/recordings').json() if s['id']==rid)['health']['runtime']
            response=client.get('/api/runtime/recordings');management=response.json()['items'][0]['runtime']
            assert health['control']==recovery['control']==management['control']=={'version':1,'status':'unconfirmed','pending_command':'pause'}
            assert health['capture']['state']==recovery['capture']['state']==management['capture']['state']=='recording'
            assert metadata==(folder/'session.json').read_bytes() and audio==(folder/'system.pcm').read_bytes()
            assert native.last_seen[rid]==123
            assert 'PRIVATE-CONTENT' not in response.text and 'PRIVATE-DEVICE-ERROR' not in response.text
            assert body['request_id'] not in response.text
            process.stdin.write.assert_not_called();process.terminate.assert_not_called()
        finally:native.processes.clear()


def test_management_prioritizes_active_and_unconfirmed_records_with_visible_limit(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        app=client.app;recordings=app.state.recording_runtime.recordings
        for i in range(24):
            s=recordings.create('audio/webm');s['created']=i+1;recordings.write(recordings.folder(s['id'])/'session.json',s)
        pending=recordings.create('audio/webm');pending.update(created=-1,control={'version':1,'pending':{'command':'pause'},'receipts':[]});recordings.write(recordings.folder(pending['id'])/'session.json',pending)
        rid,_,_=native_session(app,created=-2)
        try:
            data=client.get('/api/runtime/recordings?limit=2').json()
            assert data['total_count']==26 and data['truncated'] and data['limit']==2
            assert [s['recording_id'] for s in data['items']]==[rid,pending['id']]
            assert client.get('/api/runtime/recordings?limit=0').status_code==422
            assert client.get('/api/runtime/recordings?limit=51').status_code==422
        finally:app.state.recording_runtime.native.processes.clear()


def test_one_unreadable_projection_stays_unknown_without_hiding_other_management_rows(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        runtime=client.app.state.recording_runtime
        ids=[client.post('/api/recordings',json={}).json()['id'] for _ in range(2)]
        read=runtime.read
        def selective(rid,*args):
            if rid==ids[0]:raise OSError('PRIVATE-PATH')
            return read(rid,*args)
        monkeypatch.setattr(runtime,'read',selective)
        response=client.get('/api/runtime/recordings');rows=response.json()['items']
        assert len(rows)==2 and next(r for r in rows if r['recording_id']==ids[0])['runtime'] is None
        assert next(r for r in rows if r['recording_id']==ids[1])['available']
        assert 'PRIVATE-PATH' not in response.text
