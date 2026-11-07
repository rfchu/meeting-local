"""Real detached broker/socket and shared Swift lease. Synthetic PCM, no devices or models."""
import json,os,subprocess,sys,tempfile,time,uuid,socket,threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.capture_broker import Broker,Client,BrokerError,BrokerProcess,endpoint,read_private
from app.capture_ownership import inspect
from app.native_capture import NativeCapture
from app.recordings import RecordingStore
from app.db import Store

@pytest.fixture(scope='module')
def writer(tmp_path_factory):
    path=tmp_path_factory.mktemp('broker-writer')/'Writer'
    subprocess.run(['xcrun','swiftc','-swift-version','5','-module-cache-path',str(Path('native/build/module-cache').resolve()),'native/AudioCapture/Ownership.swift','native/BrokerWriterTests/main.swift','-o',str(path)],check=True,capture_output=True)
    return path


def until(predicate,seconds=6):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            if predicate():return
        except (OSError,ValueError):pass
        time.sleep(.03)
    raise AssertionError('synthetic broker condition timed out')

@pytest.fixture
def running(tmp_path,monkeypatch,writer):
    with tempfile.TemporaryDirectory(prefix='tj6-',dir='/private/tmp') as control:
        monkeypatch.setenv('TINGJI_CAPTURE_CONTROL_ROOT',control)
        store=Store(tmp_path);recordings=RecordingStore(store);native=NativeCapture(store,recordings)
        s=recordings.create('audio/wav');s.update(source='macos',include_microphone=False,capture_incarnation=uuid.uuid4().hex,capture_transport='broker_v2')
        folder=recordings.folder(s['id']);recordings.write(folder/'session.json',s)
        # Inject a short TTL into the production server class, never via a production HTTP test switch.
        script="from app.capture_broker import Broker;import sys;Broker(sys.argv[1],sys.argv[2],sys.argv[3],[sys.argv[4],'--output',sys.argv[5],'--incarnation',sys.argv[3]],lease_seconds=.4).run()"
        process=subprocess.Popen([sys.executable,'-c',script,str(recordings.root),s['id'],s['capture_incarnation'],str(writer),str(folder)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        client=Client(recordings.root,s['id'],s['capture_incarnation'])
        def ready():
            if process.poll() is not None:raise AssertionError(process.stderr.read().decode())
            return client.status()['alive'] and inspect(recordings.root)['lease']=='held' and (folder/'system.pcm').is_file()
        try:
            until(ready)
            yield native,s,folder,process,client
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            until(lambda:inspect(recordings.root)['lease']=='free')


def test_observation_never_claims_control_and_socket_is_private(running):
    native,s,folder,p,client=running
    value=client.status();assert value['generation']==0 and value['can_claim'] and not value['control_valid']
    assert not (client.folder/'controller.json').exists()
    assert client.path.stat().st_mode&0o777==0o600 and client.folder.stat().st_mode&0o777==0o700
    first=(folder/'system.pcm').stat().st_size
    until(lambda:(folder/'system.pcm').stat().st_size>first)


def test_fenced_recovery_rejects_old_controller_and_stale_generation(running):
    native,s,folder,p,a=running;a.claim(0)
    b=Client(native.recordings.root,s['id'],s['capture_incarnation'])
    with pytest.raises(BrokerError,match='租约'):b.claim(1)
    time.sleep(.5);b.claim(1)
    with pytest.raises(BrokerError,match='控制权'):a.command('pause',uuid.uuid4().hex)
    with pytest.raises(BrokerError,match='代次'):Client(native.recordings.root,s['id'],s['capture_incarnation']).claim(1)
    receipt=b.command('pause',uuid.uuid4().hex)['receipt'];assert receipt['outcome']=='confirmed'
    assert (folder/'commands.log').read_text().splitlines()==['pause']


def test_duplicate_request_receipts_prevent_repeated_physical_commands(running):
    native,s,folder,p,client=running;client.claim(0);request_id=uuid.uuid4().hex
    first=client.command('pause',request_id);second=client.command('pause',request_id)
    assert first['receipt']==second['receipt'] and second['replayed']
    assert (folder/'commands.log').read_text().splitlines()==['pause']
    with pytest.raises(BrokerError,match='其他操作'):client.command('resume',request_id)
    assert read_private(client.folder/'receipts.json')['receipts'][request_id]['outcome']=='confirmed'


def test_api_object_restart_observes_continuous_audio_then_explicitly_recovers_control(running):
    native,s,folder,p,client=running;client.claim(0)
    native.processes[s['id']]=BrokerProcess(client)
    before=(folder/'system.pcm').stat().st_size;native.close()
    until(lambda:(folder/'system.pcm').stat().st_size>before)
    fresh=NativeCapture(native.store,native.recordings);fresh.reconcile_interrupted()
    assert fresh.is_running(s['id']) and fresh.recordings.read(s['id'])['state']=='recording'
    status=fresh.status(s['id']);assert status['ownership']['control_required'] and not status['ownership']['controlled_by_current_service']
    with pytest.raises(ValueError):fresh.command(s['id'],'pause')
    generation=status['ownership']['controller_generation']
    assert fresh.recover_control(s['id'],generation)['ownership']['controlled_by_current_service']
    assert fresh.command(s['id'],'pause')['state']=='paused'
    assert fresh.command(s['id'],'resume')['state']=='recording'
    assert fresh.command(s['id'],'stop')['state']=='stopped'
    assert inspect(native.recordings.root)['lease']=='free'


def test_controller_process_crash_does_not_end_capture_and_recovery_waits_for_lease(running):
    native,s,folder,p,client=running
    script="from app.capture_broker import Client;import sys,time;c=Client(sys.argv[1],sys.argv[2],sys.argv[3]);c.claim(0);print('claimed',flush=True);time.sleep(60)"
    controller=subprocess.Popen([sys.executable,'-c',script,str(native.recordings.root),s['id'],s['capture_incarnation']],stdout=subprocess.PIPE)
    try:
        assert controller.stdout.readline()==b'claimed\n';controller.kill();controller.wait(timeout=5)
        before=(folder/'system.pcm').stat().st_size
        until(lambda:(folder/'system.pcm').stat().st_size>before)
        until(lambda:client.status()['can_claim'])
        client.claim(1);assert client.command('pause',uuid.uuid4().hex)['receipt']['outcome']=='confirmed'
    finally:
        if controller.poll() is None:controller.kill();controller.wait(timeout=5)


def test_broker_crash_closes_helper_pipe_and_preserves_saved_audio(running):
    native,s,folder,p,client=running
    before=(folder/'system.pcm').read_bytes();p.kill();p.wait(timeout=5)
    until(lambda:inspect(native.recordings.root)['lease']=='free')
    assert (folder/'system.pcm').read_bytes().startswith(before)
    fresh=NativeCapture(native.store,native.recordings);fresh.reconcile_interrupted()
    assert not fresh.is_running(s['id']) and fresh.recordings.read(s['id'])['state']=='interrupted'


def test_wrong_workspace_incarnation_and_unsafe_endpoint_refuse_handshake(running):
    native,s,folder,p,client=running
    wrong=Client(native.recordings.root,s['id'],'f'*32)
    with pytest.raises((BrokerError,OSError)):wrong.status()
    client.path.chmod(0o666)
    with pytest.raises(BrokerError,match='端点'):client.status()
    client.path.chmod(0o600)
    assert not (folder/'commands.log').exists()


def test_concurrent_claims_have_exactly_one_winner(running):
    native,s,folder,p,client=running
    clients=[Client(native.recordings.root,s['id'],s['capture_incarnation']) for _ in range(2)]
    def claim(c):
        try:c.claim(0);return True
        except BrokerError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sum(pool.map(claim,clients))==1


def test_expired_control_status_does_not_reauthorize_itself(running):
    native,s,folder,p,client=running;client.claim(0);time.sleep(.5)
    assert not client.status()['control_valid'] and client.credentials is None
    assert client.status()['can_claim'] and client.status()['generation']==1


def test_unconfirmed_command_stays_unconfirmed_and_is_not_replayed(running):
    native,s,folder,p,client=running;client.claim(0);(folder/'test-fault').write_text('ignore_pause')
    request_id=uuid.uuid4().hex
    first=client.command('pause',request_id)
    assert first['receipt']['outcome']=='unconfirmed'
    second=client.command('pause',request_id)
    assert second['replayed'] and second['receipt']['outcome']=='unconfirmed'
    assert (folder/'commands.log').read_text().splitlines()==['pause']


def test_same_incarnation_cannot_restart_after_broker_failure(running,writer):
    native,s,folder,p,client=running
    before=(folder/'system.pcm').read_bytes();p.kill();p.wait(timeout=5)
    until(lambda:inspect(native.recordings.root)['lease']=='free')
    second=subprocess.run([sys.executable,'-m','app.capture_broker','--root',str(native.recordings.root),'--recording-id',s['id'],'--incarnation',s['capture_incarnation'],'--helper',str(writer)],capture_output=True,timeout=6)
    assert second.returncode!=0 and inspect(native.recordings.root)['lease']=='free'
    assert (folder/'system.pcm').read_bytes().startswith(before)


def test_http_recovery_is_explicit_and_api_shutdown_keeps_writer(running):
    from fastapi.testclient import TestClient
    from app.main import create_app
    native,s,folder,p,client=running
    before=(folder/'system.pcm').stat().st_size
    with TestClient(create_app(native.store.root,start_worker=False)) as http:
        rows=http.get('/api/native-recordings').json();assert rows[0]['recording']['id']==s['id']
        status=http.get('/api/native-capture/'+s['id']).json()
        assert status['ownership']['control_required'] and not status['ownership']['controlled_by_current_service']
        path='/api/native-capture/'+s['id']+'/recover-control'
        assert http.post(path,json={'expected_generation':-1}).status_code==422
        assert http.post(path,json={'expected_generation':99}).status_code==409
        result=http.post(path,json={'expected_generation':0})
        assert result.status_code==200 and result.json()['ownership']['controlled_by_current_service']
        assert 'controller_token' not in result.text
    assert p.poll() is None
    until(lambda:(folder/'system.pcm').stat().st_size>before)


def test_real_api_process_kill_and_restart_preserves_independent_writer(running):
    import urllib.request
    native,s,folder,p,client=running
    env=os.environ.copy();env['MEETING_DATA']=str(native.store.root)
    def get(base,path,body=None):
        request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)
    apis=[]
    try:
        for generation in (0,1):
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            api=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            apis.append(api);base=f'http://127.0.0.1:{port}'
            until(lambda:get(base,'/api/native-recordings'),seconds=10)
            until(lambda:client.status()['can_claim'])
            result=get(base,'/api/native-capture/'+s['id']+'/recover-control',{'expected_generation':generation})
            assert result['ownership']['controlled_by_current_service']
            before=(folder/'system.pcm').stat().st_size
            api.kill();api.wait(timeout=5)
            until(lambda:(folder/'system.pcm').stat().st_size>before)
            assert p.poll() is None and inspect(native.recordings.root)['lease']=='held'
    finally:
        for api in apis:
            if api.poll() is None:api.kill();api.wait(timeout=5)


def test_start_uses_detached_broker_and_shutdown_releases_only_controller(running,writer,tmp_path,monkeypatch):
    import app.native_capture as module
    base,_,_,_,_=running
    native=NativeCapture(Store(tmp_path/'fresh'),RecordingStore(Store(tmp_path/'fresh')))
    monkeypatch.setattr(native,'available',lambda:True)
    monkeypatch.setattr(native,'capability',lambda:{'available':True,'ownership_protocol_version':1})
    monkeypatch.setattr(module,'HELPER',writer)
    session=native.start(False);proxy=native.processes[session['id']];launcher=proxy.launcher
    folder=native.recordings.folder(session['id'])
    try:
        until(lambda:inspect(native.recordings.root)['lease']=='held' and native.status(session['id'])['state']=='recording')
        assert session['capture_transport']=='broker_v2' and proxy.has_control
        before=(folder/'system.pcm').stat().st_size;native.close()
        until(lambda:(folder/'system.pcm').stat().st_size>before)
        assert launcher.poll() is None
        fresh=NativeCapture(native.store,native.recordings);fresh.reconcile_interrupted()
        status=fresh.status(session['id']);assert status['ownership']['control_required']
        fresh.recover_control(session['id'],status['ownership']['controller_generation'])
        assert fresh.command(session['id'],'stop')['state']=='stopped'
    finally:
        if launcher.poll() is None:launcher.terminate();launcher.wait(timeout=5)
        until(lambda:inspect(native.recordings.root)['lease']=='free')


def test_lost_reply_can_read_durable_receipt_without_repeating_command(running):
    native,s,folder,p,client=running;client.claim(0);request_id=uuid.uuid4().hex
    message={'version':2,'workspace':client.fingerprint,'recording_id':s['id'],'incarnation':s['capture_incarnation'],'operation':'command','command':'pause','request_id':request_id,'sequence':1,**client.credentials}
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.connect(str(client.path));sock.sendall(json.dumps(message).encode()+b'\n')
    until(lambda:(folder/'commands.log').exists())
    receipt=client.command('pause',request_id)
    assert receipt['replayed'] and receipt['receipt']['outcome']=='confirmed'
    assert (folder/'commands.log').read_text().splitlines()==['pause']


def test_stale_sequence_rejected_before_writer_receives_command(running):
    native,s,folder,p,client=running;client.claim(0)
    with pytest.raises(BrokerError,match='序号'):client.request('command',command='pause',request_id=uuid.uuid4().hex,sequence=0)
    assert not (folder/'commands.log').exists()


def test_wrong_peer_uid_is_rejected_before_claim(running,monkeypatch):
    import app.capture_broker as module
    native,s,folder,p,client=running
    with monkeypatch.context() as patch:
        patch.setattr(module,'peer_uid',lambda sock:os.getuid()+1)
        with pytest.raises(BrokerError,match='用户'):client.claim(0)
    assert client.status()['generation']==0 and not (client.folder/'controller.json').exists()


def test_intent_write_failure_prevents_physical_command(tmp_path,monkeypatch):
    import io
    import app.capture_broker as module
    from types import SimpleNamespace
    broker=Broker(tmp_path,'a'*32,'b'*32,[])
    broker.child=SimpleNamespace(poll=lambda:None,stdin=io.BytesIO())
    broker.generation=1;broker.controller={'controller_id':'c'*32,'controller_token':'d'*32};broker.deadline=time.monotonic()+10
    monkeypatch.setattr(module,'atomic',lambda *a:(_ for _ in ()).throw(OSError('synthetic full disk')))
    result=broker.reply({'version':2,'workspace':broker.fingerprint,'recording_id':broker.rid,'incarnation':broker.incarnation,'operation':'command','command':'pause','request_id':'e'*32,'sequence':1,'generation':1,**broker.controller})
    assert not result['ok'] and broker.child.stdin.getvalue()==b''


def test_second_broker_cannot_replace_live_socket(running,writer):
    native,s,folder,p,client=running
    second=subprocess.run([sys.executable,'-m','app.capture_broker','--root',str(native.recordings.root),'--recording-id',s['id'],'--incarnation',s['capture_incarnation'],'--helper',str(writer)],capture_output=True,timeout=6)
    assert second.returncode!=0 and client.status()['alive'] and p.poll() is None


def test_api_discovery_during_writer_startup_does_not_interrupt_session(running,writer,tmp_path):
    native,_,_,_,_=running
    store=Store(tmp_path/'startup');recordings=RecordingStore(store)
    session=recordings.create('audio/wav');session.update(source='macos',include_microphone=False,capture_incarnation=uuid.uuid4().hex,capture_transport='broker_v2')
    folder=recordings.folder(session['id']);recordings.write(folder/'session.json',session)
    script="from app.capture_broker import Broker;import sys;Broker(sys.argv[1],sys.argv[2],sys.argv[3],[sys.executable,'-c',\\\"import os,sys,time;time.sleep(.8);os.execv(sys.argv[1],sys.argv[1:])\\\",sys.argv[4],'--output',sys.argv[5],'--incarnation',sys.argv[3]]).run()"
    # Keep the script argument as code, never interpolate shell text.
    script=script.replace('\\"','"')
    broker=subprocess.Popen([sys.executable,'-c',script,str(recordings.root),session['id'],session['capture_incarnation'],str(writer),str(folder)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        client=Client(recordings.root,session['id'],session['capture_incarnation'])
        until(lambda:client.status()['alive'])
        assert inspect(recordings.root)['lease']=='free'
        fresh=NativeCapture(store,recordings);fresh.reconcile_interrupted()
        assert fresh.recordings.read(session['id'])['state']=='recording'
        until(lambda:inspect(recordings.root)['lease']=='held')
        assert fresh.is_running(session['id'])
    finally:
        if broker.poll() is None:broker.kill();broker.wait(timeout=5)
        until(lambda:inspect(recordings.root)['lease']=='free')


def test_awake_policy_belongs_to_broker_and_ends_with_writer(tmp_path,monkeypatch):
    import app.capture_broker as module
    from types import SimpleNamespace
    rid='a'*32;inc='b'*32;folder=tmp_path/rid;folder.mkdir()
    broker=Broker(tmp_path,rid,inc,[])
    broker.child=SimpleNamespace(poll=lambda:None)
    calls=[]
    class AssertionProcess:
        code=None
        def poll(self):return self.code
        def terminate(self):self.code=0
        def wait(self,timeout):return self.code
    assertion=AssertionProcess()
    monkeypatch.setattr(module.subprocess,'Popen',lambda args,**kw:(calls.append(args) or assertion))
    module.atomic(folder/'capture-policy.json',{'incarnation':inc,'keep_awake':True})
    broker.sync_awake()
    assert calls==[['/usr/bin/caffeinate','-di','-w',str(os.getpid())]] and broker.awake is assertion
    # A stale incarnation cannot alter the running assertion.
    module.atomic(folder/'capture-policy.json',{'incarnation':'c'*32,'keep_awake':False})
    broker.sync_awake();assert broker.awake is assertion
    broker.child=SimpleNamespace(poll=lambda:0);broker.sync_awake()
    assert assertion.code==0 and broker.awake is None


def test_stop_receipt_cannot_hide_missing_evidence_or_abnormal_exit(tmp_path):
    from types import SimpleNamespace
    broker=Broker(tmp_path,'a'*32,'b'*32,[]);folder=tmp_path/broker.rid;folder.mkdir()
    broker.child=SimpleNamespace(poll=lambda:0)
    assert broker.state()=='unknown' and not broker.stopped_confirmed()
    path=folder/'capture.json';normal={'state':'stopped','error':'','capture_incarnation':broker.incarnation}
    path.write_text(json.dumps(normal));assert broker.stopped_confirmed()
    for code,value in [(-9,normal),(None,normal),(0,normal|{'error':'synthetic failure'}),(0,normal|{'diagnostics':{'failure_stage':'storage'}}),(0,normal|{'capture_incarnation':'c'*32})]:
        broker.child=SimpleNamespace(poll=lambda code=code:code);path.write_text(json.dumps(value))
        assert not broker.stopped_confirmed()


def test_probe_identity_can_launch_shared_lease_writer_without_devices(tmp_path,writer,monkeypatch):
    from app.capture_probe import CaptureProbe
    store=Store(tmp_path);native=NativeCapture(store,RecordingStore(store));probe=CaptureProbe(native)
    monkeypatch.setattr(native,'available',lambda:True)
    def launch(args,**kwargs):return subprocess.Popen([str(writer),*args[1:]],**kwargs)
    probe.process_factory=launch
    result=probe.start('macos-system');item=probe.items[result['id']]
    try:
        until(lambda:inspect(item['folder'].parent)['lease']=='held')
        metadata=json.loads((item['folder']/'session.json').read_text())
        assert metadata['id']==item['folder'].name and len(metadata['id'])==32
        assert item['process'].poll() is None
    finally:probe.close()
    assert item['process'].poll() is not None


def test_independent_writer_keeps_offline_maintenance_barrier(running):
    from app.maintenance import DataLease
    native,s,folder,p,client=running
    with pytest.raises(RuntimeError):
        with DataLease(native.store.root,exclusive=True):pass
    p.kill();p.wait(timeout=5)
    until(lambda:inspect(native.recordings.root)['lease']=='free')
    with DataLease(native.store.root,exclusive=True):pass


def test_offline_backup_rechecks_orphan_writer_kernel_lease(tmp_path):
    import fcntl
    from app.backups import offline,BackupError
    root=tmp_path/'data';Store(root);recordings=root/'recordings';recordings.mkdir()
    lock=recordings/'capture-owner.lock';lock.touch(mode=0o600)
    pcm=recordings/'synthetic.pcm';pcm.write_bytes(b'\x01\x00')
    with lock.open('r') as fd:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BackupError,match='写入租约'):
            with offline(root):pass
    assert pcm.read_bytes()==b'\x01\x00'


def test_next_recording_starts_while_previous_receipt_service_is_retained(running,writer,tmp_path,monkeypatch):
    import app.native_capture as module
    native,_,_,_,_=running
    store=Store(tmp_path/'consecutive');capture=NativeCapture(store,RecordingStore(store))
    monkeypatch.setattr(capture,'available',lambda:True)
    monkeypatch.setattr(capture,'capability',lambda:{'available':True,'ownership_protocol_version':1})
    monkeypatch.setattr(module,'HELPER',writer)
    launchers=[]
    try:
        first=capture.start(False);proxy=capture.processes[first['id']];launchers.append(proxy.launcher)
        until(lambda:capture.status(first['id'])['state']=='recording')
        assert capture.command(first['id'],'stop')['state']=='stopped'
        assert proxy.launcher.poll() is None  # Old service retains receipt queries.
        second=capture.start(False);other=capture.processes[second['id']];launchers.append(other.launcher)
        until(lambda:capture.status(second['id'])['state']=='recording')
        assert first['id']!=second['id'] and proxy.client.path!=other.client.path
        # Once the old service exits, its private terminal/receipt files remain authoritative.
        receipts=read_private(proxy.client.folder/'receipts.json')['receipts'];request_id=list(receipts)[-1]
        proxy.launcher.terminate();proxy.launcher.wait(timeout=5)
        assert proxy.client.status()['exit_code']==0
        assert proxy.client.command('stop',request_id)['replayed']
        assert proxy.poll()==0 and proxy.exit_code_known
        assert capture.is_running(second['id'])
        assert capture.command(second['id'],'stop')['state']=='stopped'
    finally:
        for launcher in launchers:
            if launcher.poll() is None:launcher.kill();launcher.wait(timeout=5)
        until(lambda:inspect(capture.recordings.root)['lease']=='free')


def test_missing_broker_does_not_fabricate_known_writer_exit(tmp_path):
    root=tmp_path/'recordings';root.mkdir()
    proxy=BrokerProcess(Client(root,'a'*32,'b'*32))
    assert proxy.poll() is not None and proxy.exit_code_known is False
