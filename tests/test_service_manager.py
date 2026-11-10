import plistlib
from types import SimpleNamespace
import pytest
from scripts.manage_service import MacService


def manager(tmp_path,monkeypatch):
    monkeypatch.delenv('MEETING_DATA',raising=False);monkeypatch.delenv('PORT',raising=False)
    python=tmp_path/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
    return MacService(tmp_path)


def test_existing_service_never_starts_duplicate_or_rewrites_config(tmp_path,monkeypatch):
    service=manager(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'status',lambda:{'loaded':True,'running':True,'pid':123})
    monkeypatch.setattr(service,'call',lambda *_:pytest.fail('already running'))
    assert service.start()['pid']==123
    assert not service.plist.exists()


def test_portable_status_does_not_inspect_the_development_launch_agent(tmp_path,monkeypatch):
    monkeypatch.setenv('TINGJI_PORTABLE_SERVICE','1')
    service=manager(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'call',lambda *_:pytest.fail('portable runtime queried development launchd'))
    assert service.status()=={'loaded':False,'running':False,'port':service.port}


def test_other_listener_is_preserved(tmp_path,monkeypatch):
    service=manager(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'status',lambda:{'loaded':False,'running':False})
    monkeypatch.setattr(service,'occupied',lambda:True)
    with pytest.raises(ValueError,match='占用'):service.start()
    assert not service.plist.exists()


def test_launchd_service_is_local_and_not_a_login_item(tmp_path,monkeypatch):
    service=manager(tmp_path,monkeypatch);commands=[]
    monkeypatch.setattr(service,'status',lambda:{'loaded':False,'running':False})
    monkeypatch.setattr(service,'occupied',lambda:False)
    monkeypatch.setattr(service,'call',lambda *args:commands.append(args) or SimpleNamespace(returncode=0))
    service.start();definition=plistlib.loads(service.plist.read_bytes())
    assert commands[0][0]=='bootstrap' and definition['KeepAlive']=={'SuccessfulExit':False}
    assert definition['ProgramArguments'][definition['ProgramArguments'].index('--host')+1]=='127.0.0.1'
    assert service.plist.parent==tmp_path/'data/services'
    assert service.plist.stat().st_mode&0o777==0o600
    assert 'API_KEY' not in str(definition)


def test_stop_refuses_to_interrupt_recording_by_default(tmp_path,monkeypatch):
    service=manager(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'status',lambda:{'loaded':True,'running':True})
    monkeypatch.setattr(service,'busy',lambda:True)
    monkeypatch.setattr(service,'call',lambda *_:pytest.fail('should not stop'))
    with pytest.raises(ValueError,match='录音'):service.stop()

def test_detached_start_owns_new_session_and_never_serializes_environment(tmp_path,monkeypatch):
    import json
    service=manager(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(service,'status',lambda:{'loaded':False,'running':False})
    monkeypatch.setattr(service,'occupied',lambda:False)
    monkeypatch.setenv('MEETING_LLM_API_KEY','sentinel-secret')
    monkeypatch.setattr('scripts.manage_service.subprocess.Popen',lambda args,**kw:calls.append(kw) or SimpleNamespace(pid=12345,poll=lambda:None))
    status=service.start(detached=True)
    assert status['backend']=='detached' and calls[0]['start_new_session'] and calls[0]['close_fds']
    assert 'sentinel-secret' not in service.record.read_text()
    assert json.loads(service.record.read_text())['pid']==12345

def test_portable_service_uses_workspace_as_working_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('TINGJI_PORTABLE_SERVICE', '1')
    service = manager(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(service, 'occupied', lambda: False)
    monkeypatch.setattr('scripts.manage_service.subprocess.Popen',
                        lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(pid=12345, poll=lambda: None))

    service.start()

    args, options = calls[0]
    assert options['cwd'] == service.data
    assert args[1] == '-P'
    assert args[args.index('--app-dir') + 1] == str(service.root)


def test_detached_stop_refuses_unverified_or_recycled_pid(tmp_path,monkeypatch):
    service=manager(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'status',lambda:{'loaded':True,'running':True,'ready':False,'backend':'detached','pid':12345})
    monkeypatch.setattr(service,'busy',lambda:False)
    monkeypatch.setattr('scripts.manage_service.os.kill',lambda *_:pytest.fail('unverified PID'))
    with pytest.raises(ValueError,match='身份'):service.stop(force=True)

def test_process_permission_failure_is_unknown_not_stopped(tmp_path,monkeypatch):
    import json
    service=manager(tmp_path,monkeypatch);service.folder.mkdir(parents=True)
    service.record.write_text(json.dumps({'pid':12345,'instance_id':'instance'}))
    def denied(*_):raise PermissionError('denied')
    monkeypatch.setattr('scripts.manage_service.os.kill',denied)
    state=service.detached_status()
    assert state['loaded'] and state['running'] is None
    with pytest.raises(ValueError,match='第二个'):service.start(detached=True)


def test_stop_checks_independent_writer_even_without_recent_session(tmp_path,monkeypatch):
    import fcntl,os
    service=manager(tmp_path,monkeypatch);folder=service.data/'recordings';folder.mkdir(parents=True)
    lock=folder/'capture-owner.lock'
    with lock.open('w') as handle:
        os.chmod(lock,0o600);fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert service.busy()
        monkeypatch.setattr(service,'status',lambda:{'loaded':True,'running':True})
        with pytest.raises(ValueError,match='录音'):service.stop()
    assert not service.busy()


def test_uncertain_writer_blocks_normal_service_stop(tmp_path,monkeypatch):
    service=manager(tmp_path,monkeypatch)
    monkeypatch.setattr('app.capture_ownership.inspect',lambda root:{'lease':'unknown'})
    assert service.busy()


def test_maintenance_uses_capture_content_and_audio_instead_of_session_mtime(tmp_path, monkeypatch):
    import json
    import os
    from app.host_bridge import maintenance_report

    service = manager(tmp_path, monkeypatch)
    folder = service.data / 'recordings' / 'old'
    folder.mkdir(parents=True)
    (folder / 'session.json').write_text(json.dumps({'state': 'recording', 'updated': 100}))
    assert maintenance_report(service.data, now=1000) == {'blockers': [], 'recoverable': 1}
    audio = folder / '00000000.part'
    audio.write_bytes(b'audio')
    os.utime(audio, (999, 999))
    assert maintenance_report(service.data, now=1000)['blockers'] == ['capture_active']
    assert audio.read_bytes() == b'audio'


@pytest.mark.parametrize('backend', ['detached', 'launchd'])
def test_restart_preserves_backend_and_waits_for_new_identity(tmp_path, monkeypatch, backend):
    service = manager(tmp_path, monkeypatch)
    calls = []
    states = iter([
        {'loaded': True, 'ready': True, 'pid': 123, 'backend': backend},
        {'loaded': False},
        {'loaded': True, 'ready': False, 'pid': 234, 'backend': backend},
        {'loaded': True, 'ready': True, 'pid': 234, 'backend': backend},
    ])
    monkeypatch.setattr(service, 'status', lambda: next(states))
    monkeypatch.setattr(service, 'occupied', lambda: False)
    monkeypatch.setattr(service, 'stop', lambda **args: calls.append(args))
    monkeypatch.setattr(service, 'start', lambda **args: calls.append(args) or {'pid': 234})
    monkeypatch.setattr('scripts.manage_service.time.sleep', lambda _: None)
    assert service.restart(expected_pid=123)['pid'] == 234
    assert calls == [{'expected_pid': 123}, {'detached': backend == 'detached'}]


def test_restart_reports_blockers_before_stopping(tmp_path, monkeypatch):
    from app.db import Store
    from app.recordings import RecordingStore
    from scripts.manage_service import RestartBlocked

    service = manager(tmp_path, monkeypatch)
    RecordingStore(Store(service.data)).create('audio/webm')
    monkeypatch.setattr(service, 'status', lambda: {
        'loaded': True, 'ready': True, 'pid': 123, 'backend': 'detached',
    })
    monkeypatch.setattr(service, 'stop', lambda **_: pytest.fail('should not stop'))
    with pytest.raises(RestartBlocked) as error:
        service.restart(expected_pid=123)
    assert error.value.reasons == ['capture_active']
    with pytest.raises(RestartBlocked) as error:
        service.restart(expected_pid=999)
    assert error.value.reasons == ['service_unverified']
