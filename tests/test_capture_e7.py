"""E7: synthetic recovery and artifact gates; never opens capture devices/models."""
import asyncio,json,os,subprocess
from pathlib import Path
import pytest
from app.capture_binary import BinaryLease
from tests.test_live_session import setup,CONFIG,audio,until


def native_session(manager,recordings,rid):
    session=recordings.read(rid);session.update(source='macos',state='recording',duration=10)
    recordings.write(recordings.folder(rid)/'session.json',session)
    recordings.write(recordings.folder(rid)/'capture.json',{'state':'recording','duration':10})
    manager.native.status=lambda *a,**kw:{'state':'recording','duration':10}


def test_recovery_is_passive_and_preserves_config_cursor_coverage(tmp_path):
    async def run():
        manager,recordings,rid,resident,_=setup(tmp_path)
        manager.ensure=lambda rid:None
        await manager.start(rid,CONFIG)
        manager.ingest(rid,0,audio(3))
        native_session(manager,recordings,rid)
        manager.store.execute("UPDATE live_sessions SET source='native' WHERE recording_id=?",(rid,))
        manager.checkpoint(rid,status='stopped',cursor=48000,partial={'text':'未确认'},metrics={'old':'keep'})
        before=manager.row(rid);projection=manager.recovery_status(rid)
        assert projection['recoverable'] and projection['coverage']['gaps']==[{'start':0,'end':10}]
        assert manager.row(rid)==before and resident.calls==[]
        scheduled=[];manager.ensure=lambda rid:scheduled.append(rid)
        recovered=await manager.recover(rid,before['seq'])
        after=manager.row(rid)
        assert scheduled==[rid] and after['cursor']==48000 and after['config']==before['config']
        assert json.loads(after['partial'])=={} and json.loads(after['metrics'])['old']=='keep'
        assert recovered['coverage']==projection['coverage']
        with pytest.raises(ValueError,match='已变化'):await manager.recover(rid,before['seq'])
        assert resident.calls==[]
        await manager.close()
    asyncio.run(run())


def test_active_or_finished_capture_cannot_be_recovered(tmp_path):
    async def run():
        manager,recordings,rid,_,_=setup(tmp_path);manager.ensure=lambda rid:None
        await manager.start(rid,CONFIG);native_session(manager,recordings,rid)
        manager.store.execute("UPDATE live_sessions SET source='native' WHERE recording_id=?",(rid,))
        task=asyncio.create_task(asyncio.sleep(20));manager.tasks[rid]=task
        with pytest.raises(ValueError,match='无需恢复'):await manager.recover(rid,manager.row(rid)['seq'])
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        manager.native.status=lambda *a,**kw:{'state':'stopped'}
        with pytest.raises(ValueError,match='采集未运行'):await manager.recover(rid,manager.row(rid)['seq'])
        await manager.close()
    asyncio.run(run())


def test_restart_recovery_tracks_first_confirmed_result_without_rewinding(tmp_path):
    async def run():
        manager,recordings,rid,_,_=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            manager.checkpoint(rid,cursor=3*16000,status='paused')
            await manager.close()
            manager.closed=False
            manager.ingest(rid,3,audio(2))
            await manager.resume()
            await until(lambda:manager.row(rid)['cursor']==5*16000)
            marker=json.loads(manager.row(rid)['metrics'])['recovery']
            assert marker['reason']=='service_restart' and marker['from_frame']==48000 and marker['state']=='resumed'
            assert manager.cache.overview(rid,'qwen',5)['gaps']==[{'start':0,'end':3}]
        finally:await manager.close()
    asyncio.run(run())


@pytest.fixture(scope='module')
def artifact_harness(tmp_path_factory):
    folder=tmp_path_factory.mktemp('artifact-swift');binary=folder/'gate'
    subprocess.run(['xcrun','swiftc','-swift-version','5','-module-cache-path',str(folder/'modules'),'native/AudioCapture/ArtifactLease.swift','native/ArtifactLeaseTests/main.swift','-o',str(binary)],check=True,capture_output=True)
    return binary


def test_swift_and_python_share_upgrade_gate(tmp_path,monkeypatch,artifact_harness):
    monkeypatch.setenv('TINGJI_CAPTURE_DELIVERY_ROOT',str(tmp_path/'gates'))
    executable=tmp_path/'capture';executable.write_text('synthetic')
    with BinaryLease(executable,exclusive=True):
        blocked=subprocess.run([str(artifact_harness),str(executable)],capture_output=True,text=True)
        assert blocked.returncode==1 and blocked.stdout.strip()=='blocked'
    process=subprocess.Popen([str(artifact_harness),str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=='held'
        with pytest.raises(ValueError,match='正在运行'):BinaryLease(executable,exclusive=True).acquire()
        with BinaryLease(executable):pass
    finally:
        process.communicate('\n',timeout=5)
    with BinaryLease(executable,exclusive=True):pass


def test_unsafe_artifact_gate_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setenv('TINGJI_CAPTURE_DELIVERY_ROOT',str(tmp_path/'gates'))
    gate=BinaryLease(tmp_path/'capture');gate.folder.parent.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='权限不安全'):gate.acquire()


def test_capture_delivery_upgrade_rollback_and_live_gate(tmp_path,monkeypatch):
    from scripts.capture_delivery import CaptureDelivery
    monkeypatch.setenv('TINGJI_CAPTURE_DELIVERY_ROOT',str(tmp_path/'gates'))
    def bundle(path,text):
        (path/'Contents/MacOS').mkdir(parents=True)
        (path/'Contents/MacOS/TingjiCapture').write_text(text)
    target=tmp_path/'TingjiCapture.app';bundle(target,'old');d=CaptureDelivery(target)
    monkeypatch.setattr(d,'verify',lambda app:{'CFBundleVersion':'4'})
    monkeypatch.setattr(d,'runtime',lambda app:{'synthetic':True})
    monkeypatch.setattr(d,'running',lambda app:None)
    monkeypatch.setattr(d,'compile',lambda app:bundle(app,'new'))
    executable=target/'Contents/MacOS/TingjiCapture'
    with BinaryLease(executable):
        with pytest.raises(ValueError,match='正在运行'):d.apply()
    assert executable.read_text()=='old'
    first=d.apply();assert executable.read_text()=='new'
    assert d.status()['host_version']=='4'
    d.apply(first['release']);assert executable.read_text()=='old'


def test_management_read_is_passive_and_recover_route_is_version_checked(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    monkeypatch.setenv('TINGJI_CAPTURE_CONTROL_ROOT',str(tmp_path/'control'))
    app=create_app(tmp_path/'data',start_worker=False);manager=app.state.services.live_sessions
    recordings=app.state.services.recordings;rid=recordings.create('audio/wav')['id']
    native_session(manager,recordings,rid)
    manager.store.execute("INSERT INTO live_sessions(recording_id,source,config,status) VALUES(?,?,?,?)",(rid,'native',json.dumps(dict(CONFIG,engine='qwen')), 'stopped'))
    scheduled=[];manager.ensure=lambda rid:scheduled.append(rid)
    with TestClient(app) as client:
        before=manager.row(rid)
        status=client.get('/api/runtime/capture-control')
        assert status.status_code==200 and status.json()['passive']
        assert status.json()['sessions'][0]['recoverable'] and not scheduled and manager.row(rid)==before
        url=f'/api/recordings/{rid}/live-session/recover'
        assert client.post(url,json={'expected_seq':-1}).status_code==422
        assert client.post(url,json={'expected_seq':before['seq']+1}).status_code==409
        assert client.post(url,json={'expected_seq':before['seq']}).status_code==200
        assert scheduled==[rid]
        assert client.post(url,json={'expected_seq':before['seq']}).status_code==409
