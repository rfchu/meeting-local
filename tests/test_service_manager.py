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
