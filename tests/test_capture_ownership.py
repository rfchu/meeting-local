"""E5 real kernel lease and writer faults; synthetic PCM only, no device API imports."""
import fcntl,hashlib,json,os,signal,subprocess,sys,time,uuid
from pathlib import Path
import pytest
from app.capture_ownership import inspect,blocks_publication
from app.native_capture import NativeCapture
from app.recordings import RecordingStore
from app.db import Store

@pytest.fixture(scope='module')
def helper(tmp_path_factory):
    target=tmp_path_factory.mktemp('ownership')/'OwnerTests'
    subprocess.run(['xcrun','swiftc','-swift-version','5','-module-cache-path',str(Path('native/build/module-cache').resolve()),'native/AudioCapture/Ownership.swift','native/CaptureOwnershipTests/main.swift','-o',str(target)],check=True,capture_output=True)
    return target

@pytest.fixture
def environment(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store);native=NativeCapture(store,recordings)
    s=recordings.create('audio/wav');s.update(source='macos',include_microphone=False,capture_incarnation=uuid.uuid4().hex)
    folder=recordings.folder(s['id']);RecordingStore.write(folder/'session.json',s)
    return native,s,folder


def start(helper,s,folder):
    p=subprocess.Popen([str(helper),str(folder),s['capture_incarnation']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert p.stdout.readline()==b'held\n'
    return p


def stop(p):
    if p.poll() is None:
        p.stdin.close();p.wait(timeout=5)
    if p.poll() is None:p.kill();p.wait(timeout=3)


def test_free_is_read_only_and_stale_pid_does_not_grant_authority(tmp_path):
    assert inspect(tmp_path)['lease']=='free';assert not list(tmp_path.iterdir())
    (tmp_path/'capture-owner.json').write_text(json.dumps({'pid':os.getpid()}))
    assert inspect(tmp_path)['lease']=='free'


def test_writer_holds_lease_and_second_writer_rejected_before_pcm(helper,environment):
    native,s,folder=environment;p=start(helper,s,folder)
    try:
        view=native.ownership();assert view['lease']=='held' and view['recording_id']==s['id'] and not view['reattach_supported']
        assert view['incarnation']==s['capture_incarnation']
        native.processes[s['id']]=p
        assert native.ownership()['decision']=='controlled'
        assert native.status(s['id'])['ownership']['controlled_by_current_service'] is True
        rejected=subprocess.run([str(helper),str(folder),s['capture_incarnation']],input=b'',capture_output=True,timeout=5)
        assert rejected.returncode==3 and not (folder/'system.pcm').exists()
        assert (folder.parent/'capture-owner.json').stat().st_mode&0o777==0o600
        p.stdin.write(b'write\n');p.stdin.flush();assert p.stdout.readline()==b'written\n'
        assert (folder/'system.pcm').read_bytes()==b'\x01\x00\x02\x00'
    finally:stop(p)
    assert native.ownership()['lease']=='free'


def test_api_recreation_does_not_attach_signal_or_publish_active_writer(helper,environment):
    native,s,folder=environment;p=start(helper,s,folder)
    try:
        fresh=NativeCapture(native.store,native.recordings);fresh.reconcile_interrupted()
        assert fresh.recordings.read(s['id'])['state']=='interrupted'
        assert fresh.status(s['id'])['ownership']['controlled_by_current_service'] is False
        with pytest.raises(ValueError,match='所有权'):fresh.finish(s['id'])
        for command in ('stop','pause','resume'):
            with pytest.raises(ValueError,match='控制权'):fresh.command(s['id'],command)
        assert p.poll() is None and not fresh.processes
        assert not native.store.one('SELECT id FROM meetings WHERE id=?',(s['id'],))
        p.stdin.write(b'write\n');p.stdin.flush();assert p.stdout.readline()==b'written\n'
        assert (folder/'system.pcm').stat().st_size==4
    finally:stop(p)
    assert native.ownership()['lease']=='free'


@pytest.mark.parametrize('end',['eof','kill'])
def test_controller_eof_or_writer_crash_releases_kernel_lease_and_preserves_pcm(helper,environment,end):
    native,s,folder=environment;p=start(helper,s,folder)
    p.stdin.write(b'write\n');p.stdin.flush();assert p.stdout.readline()==b'written\n'
    before=(folder/'system.pcm').read_bytes()
    if end=='eof':p.stdin.close()
    else:p.kill()
    p.wait(timeout=5)
    assert native.ownership()['lease']=='free' and (folder/'system.pcm').read_bytes()==before
    # Old owner metadata remains diagnostic only and cannot block a released kernel lease.
    assert (folder.parent/'capture-owner.json').exists()


def test_bad_incarnation_and_cross_session_identity_never_take_lease(helper,environment):
    native,s,folder=environment
    p=subprocess.run([str(helper),str(folder),'f'*32],input=b'',capture_output=True,timeout=5)
    assert p.returncode==3 and native.ownership()['lease']=='free' and not (folder/'system.pcm').exists()


@pytest.mark.parametrize('problem',['bad_json','non_object','wrong_workspace','symlink','public_mode','wrong_protocol'])
def test_unknown_locked_owner_blocks_conservatively(environment,problem):
    native,s,folder=environment;root=folder.parent
    lock=root/'capture-owner.lock';lock.touch(mode=0o600)
    value={'protocol_version':1,'recording_id':s['id'],'incarnation':s['capture_incarnation'],
           'workspace_fingerprint':hashlib.sha256(str(root.resolve()).encode()).hexdigest(),'transport':'stdin_v1','reattach':False}
    meta=root/'capture-owner.json'
    if problem=='wrong_workspace':value['workspace_fingerprint']='0'*64
    if problem=='wrong_protocol':value['protocol_version']=99
    meta.write_text('{' if problem=='bad_json' else '[]' if problem=='non_object' else json.dumps(value));meta.chmod(0o600)
    if problem=='public_mode':meta.chmod(0o644)
    if problem=='symlink':meta.unlink();meta.symlink_to(root/'missing')
    with lock.open('r') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=native.ownership();assert result['lease']!='free' and result['decision']=='blocked_unknown_owner'
        assert blocks_publication(result,s['id'])


def test_start_refuses_unowned_writer_before_creating_session(helper,environment,monkeypatch):
    native,s,folder=environment;p=start(helper,s,folder)
    monkeypatch.setattr(native,'available',lambda:True)
    before=len(native.recordings.list())
    try:
        with pytest.raises(ValueError,match='所有权'):native.start(False)
        assert len(native.recordings.list())==before and p.poll() is None
    finally:stop(p)


def test_legacy_helper_requires_upgrade_before_new_device_session(environment,monkeypatch):
    native,s,folder=environment;monkeypatch.setattr(native,'available',lambda:True);monkeypatch.setattr(native,'capability',lambda:{'available':True,'helper_build':2})
    before=len(native.recordings.list())
    with pytest.raises(ValueError,match='更新'):native.start(False)
    assert len(native.recordings.list())==before


def test_controller_process_crash_closes_pipe_releases_writer_and_preserves_audio(helper,environment):
    native,s,folder=environment
    script="""import subprocess,sys,time
p=subprocess.Popen(sys.argv[1:],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
assert p.stdout.readline()==b'held\\n'
p.stdin.write(b'write\\n');p.stdin.flush();assert p.stdout.readline()==b'written\\n'
print('controller-ready',flush=True)
while True:time.sleep(1)
"""
    controller=subprocess.Popen([sys.executable,'-c',script,str(helper),str(folder),s['capture_incarnation']],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        assert controller.stdout.readline()==b'controller-ready\n'
        assert native.ownership()['lease']=='held';before=(folder/'system.pcm').read_bytes()
        controller.kill();controller.wait(timeout=5)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and native.ownership()['lease']!='free':time.sleep(.02)
        assert native.ownership()['lease']=='free'
        fresh=NativeCapture(native.store,native.recordings);fresh.reconcile_interrupted()
        assert fresh.recordings.read(s['id'])['state']=='interrupted' and not fresh.processes
        assert (folder/'system.pcm').read_bytes()==before
    finally:
        if controller.poll() is None:controller.kill();controller.wait(timeout=3)


def test_start_persists_identity_before_launch_and_keeps_owned_control(helper,environment,monkeypatch):
    import app.native_capture as module
    native,_,_=environment
    monkeypatch.setattr(native,'available',lambda:True)
    monkeypatch.setattr(native,'capability',lambda:{'available':True,'ownership_protocol_version':1})
    original=subprocess.Popen
    def launch(args,**kwargs):
        folder=Path(args[args.index('--output')+1]);incarnation=args[args.index('--incarnation')+1]
        assert json.loads((folder/'session.json').read_text())['capture_incarnation']==incarnation
        process=original([str(helper),str(folder),incarnation],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert process.stdout.readline()==b'held\n';return process
    monkeypatch.setattr(module.subprocess,'Popen',launch)
    session=native._start_direct(False);process=native.processes[session['id']]
    try:
        assert native.ownership()['decision']=='controlled'
        assert native.status(session['id'])['ownership']['controlled_by_current_service'] is True
    finally:stop(process)
    assert native.ownership()['lease']=='free'
