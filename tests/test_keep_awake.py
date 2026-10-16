import os
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app.keep_awake import KeepAwake
from app.main import create_app


@pytest.fixture
def manager(monkeypatch):
    processes=[]
    def spawn(command,**kwargs):
        assert command==['/usr/bin/caffeinate','-di','-w',str(os.getpid())]
        process=Mock();process.poll.return_value=None
        process.terminate.side_effect=lambda:setattr(process.poll,'return_value',0)
        processes.append(process)
        return process
    monkeypatch.setattr('app.keep_awake.subprocess.Popen',spawn)
    awake=KeepAwake();awake.supported=True
    yield awake,processes
    awake.close()


def test_shared_assertion_renews_and_releases_last_session(manager):
    awake,processes=manager
    assert awake.acquire('a')['active']
    awake.acquire('a');awake.acquire('b')
    assert len(processes)==1
    assert not awake.release('a')['active']
    assert awake.status('b')['active']
    processes[0].terminate.assert_not_called()
    awake.release('b');awake.release('b')
    processes[0].terminate.assert_called_once()
    assert awake.process is None


def test_expiry_retains_live_native_recording_then_cleans_up(manager,monkeypatch):
    awake,processes=manager
    now=[1000];monkeypatch.setattr('app.keep_awake.time.monotonic',lambda:now[0])
    awake.native_alive=lambda rid:rid=='native'
    awake.acquire('browser');awake.acquire('native')
    now[0]+=181;awake.expire()
    assert not awake.status('browser')['active']
    assert awake.status('native')['active']
    awake.native_alive=lambda _:False;awake.expire()
    processes[0].terminate.assert_called_once()


def test_completed_recording_and_shutdown_release_even_fresh_leases(manager):
    awake,processes=manager
    awake.acquire('a');awake.is_open=lambda _:False;awake.expire()
    assert not awake.status('a')['active']
    awake.acquire('b');awake.close()
    assert not awake.status('b')['active']
    with pytest.raises(ValueError,match='已关闭'):awake.acquire('c')
    assert all(process.terminate.call_count==1 for process in processes)


def test_unsupported_and_failed_assertions_report_inactive_and_can_retry(manager,monkeypatch):
    awake,processes=manager;awake.supported=False
    assert not awake.acquire('a')['active'] and not processes
    awake.supported=True
    with monkeypatch.context() as patch:
        patch.setattr('app.keep_awake.subprocess.Popen',Mock(side_effect=OSError('denied')))
        assert not awake.acquire('a')['active']
        assert '失败' in awake.status('a')['error']
    assert awake.acquire('a')['active'] and not awake.status('a')['error']
    processes[0].poll.return_value=1
    assert awake.acquire('a')['active'] and len(processes)==2


def test_api_rejects_missing_and_completed_recordings_and_releases_on_finish(tmp_path,manager):
    application=create_app(tmp_path,start_worker=False)
    # Exercise real routes and lifespan while replacing only the OS process factory.
    application.state.keep_awake.supported=True
    with TestClient(application) as client:
        route=lambda rid:f'/api/recordings/{rid}/keep-awake'
        assert client.post(route('0'*32),json={'enabled':True}).status_code==409
        rid=client.post('/api/recordings',json={}).json()['id']
        assert client.post(route(rid),json={'enabled':True}).json()['active']
        assert not client.post(route(rid),json={'enabled':False}).json()['active']
        assert client.post(route(rid),json={'enabled':True}).json()['active']
        client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'recorded-bytes','audio/webm')})
        result=client.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1})
        assert result.status_code==200,result.text
        assert not application.state.keep_awake.status(rid)['active']
        assert client.post(route(rid),json={'enabled':True}).status_code==409
        assert client.post(route(rid),json={'enabled':False}).status_code==200
