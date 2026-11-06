"""C2 admission, cooperative checkpoints and cancellation races; synthetic data only."""
import json,threading,time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.db import Store,uid,encode
from app.resources import ResourceScheduler,ResourceGate,optional_slot,ResourceBusy
from app.resource_policy import sync_live_demand
from app.worker import Worker,Yielded
from app.main import create_app
from app.recordings import RecordingStore


def test_live_demand_reserves_admission_between_asr_windows_and_pause_releases():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler);scheduler.set_realtime(['private-session'])
    background=gate.request('analysis',{'local_llm':1})
    assert not background.acquire(False) and background.reason=='realtime_priority'
    assert not any(scheduler.used.values())
    assert gate.acquire(False);gate.release()
    scheduler.set_realtime([])
    assert background.acquire(False);background.close()
    assert not scheduler.snapshot()['requests']


def test_priority_and_fifo_apply_to_conflicting_bundles_without_reserving_partial_slots():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler)
    with gate.request():
        first=gate.request('analysis',{'local_llm':1});second=gate.request('analysis',{'local_llm':1});live=gate.request()
        assert not first.acquire(False) and not second.acquire(False) and not live.acquire(False)
    assert not first.acquire(False) and first.reason=='priority_queue'
    assert live.acquire(False);live.close()
    assert not second.acquire(False) and second.reason=='priority_queue'
    assert first.acquire(False);first.close()
    assert second.acquire(False);second.close()
    assert not any(scheduler.used.values())


def test_waiting_cancel_wakes_context_but_never_frees_an_active_holder():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler)
    active=gate.request('analysis',{'local_llm':1},'job');active.acquire()
    wait=gate.request('analysis',{'local_llm':1},'waiter');events=[]
    def run():
        try:
            with wait:events.append('must not execute')
        except ResourceBusy:events.append('withdrawn')
    thread=threading.Thread(target=run);thread.start()
    deadline=time.monotonic()+1
    while wait.state!='waiting' and time.monotonic()<deadline:time.sleep(.002)
    assert scheduler.cancel_waiting('job')==0
    assert scheduler.cancel_waiting('waiter')==1
    thread.join(1)
    assert not thread.is_alive() and events==['withdrawn']
    assert scheduler.used['legacy_inference']==1
    active.close();assert not any(scheduler.used.values())


def test_disjoint_resources_can_run_and_local_compatibility_defers_while_cloud_can_proceed():
    scheduler=ResourceScheduler();scheduler.set_realtime(['session'])
    with pytest.raises(ResourceBusy):
        with optional_slot(scheduler,'minutes','local_llm',legacy=True):pytest.fail('local generation must defer')
    with optional_slot(scheduler,'minutes','cloud_llm'):
        with scheduler.request('live_asr',{'asr':1,'legacy_inference':1}):
            assert scheduler.used['cloud_llm']==1 and scheduler.used['asr']==1
    assert not any(scheduler.used.values())


@pytest.mark.parametrize('source',['browser','native'])
def test_pause_drains_known_tail_before_allowing_background_work(tmp_path,source):
    store=Store(tmp_path);recordings=RecordingStore(store);rid=recordings.create('audio/wav')['id']
    store.execute("INSERT INTO live_sessions(recording_id,source,status,config,cursor) VALUES(?,?,'paused','{}',0)",(rid,source))
    if source=='native':recordings.write(recordings.folder(rid)/'capture.json',{'state':'paused','duration':1})
    else:store.execute('INSERT INTO live_pcm_inputs VALUES(?,?,?,?)',(rid,0,16000,'synthetic'))
    scheduler=ResourceScheduler();sync_live_demand(scheduler,store)
    assert scheduler.snapshot()['live_session_count']==1
    store.execute('UPDATE live_sessions SET cursor=16000 WHERE recording_id=?',(rid,));sync_live_demand(scheduler,store)
    assert scheduler.snapshot()['live_session_count']==0
    store.execute("UPDATE live_sessions SET status='draining' WHERE recording_id=?",(rid,));sync_live_demand(scheduler,store)
    assert scheduler.snapshot()['live_session_count']==1
    scheduler.close();sync_live_demand(scheduler,store);assert scheduler.snapshot()['live_session_count']==0


def test_native_pause_in_another_page_is_observed_even_if_session_row_still_running(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store);rid=recordings.create('audio/wav')['id']
    store.execute("INSERT INTO live_sessions(recording_id,source,status,config,cursor) VALUES(?,'native','running','{}',16000)",(rid,))
    recordings.write(recordings.folder(rid)/'capture.json',{'state':'paused','duration':1})
    scheduler=ResourceScheduler();sync_live_demand(scheduler,store);assert not scheduler.live_owners
    recordings.write(recordings.folder(rid)/'capture.json',{'state':'recording','duration':1})
    sync_live_demand(scheduler,store);assert scheduler.live_owners


def test_cancel_endpoint_withdraws_waiting_job_only_and_prevents_early_retry(tmp_path):
    app=create_app(tmp_path,start_worker=False);store=app.state.store;scheduler=app.state.services.resources
    mid=uid();jid=uid();store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'synthetic','test.wav'))
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'running','analysis','{}')",(jid,mid))
    app.state.worker.busy_jid=jid
    with TestClient(app) as client, scheduler.request('live_overview',{'local_llm':1}):
        request=app.state.services.live_asr_lock.request('analysis',{'local_llm':1},jid);assert not request.acquire(False)
        assert client.post('/api/jobs/'+jid+'/cancel').status_code==200
        assert request.state=='closed' and scheduler.used['local_llm']==1
        assert client.post('/api/jobs/'+jid+'/retry').status_code==409
        assert client.post('/api/jobs/'+jid+'/cancel').status_code==200
        assert store.one('SELECT count(*) AS n FROM job_runtime_events')['n']==1
    app.state.worker.busy_jid=None


def test_cooperative_yield_requeues_and_reuses_validated_analysis_checkpoint(tmp_path,monkeypatch):
    from app.analysis import MeetingAnalysis
    store=Store(tmp_path);scheduler=ResourceScheduler();worker=Worker(store);worker.resources=scheduler;worker.asr_lock=ResourceGate(scheduler)
    mid=uid();jid=uid();rid=uid();store.directory(mid).mkdir()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'checkpoint fixture','test.wav'))
    segments=[{'id':f's{i}','start':i,'end':i+1,'speaker':'甲','text':'合成检查点验证'+str(i)*50} for i in range(3)]
    revision=store.save_revision(mid,segments,'manual')
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'queued','analysis',?)",(jid,mid,encode({'revision':revision,'model':'synthetic','max_chars':100})))
    calls=[];yielded=threading.Event()
    class Fake:
        def models(self):return ['synthetic']
        def analyze_chunk(self,model,chunk,*args):
            calls.append(chunk[0]['id'])
            if len(calls)==1:
                store.execute("INSERT INTO live_sessions(recording_id,source,status,config) VALUES(?,'browser','running','{}')",(rid,))
                scheduler.set_realtime([rid]);yielded.set()
            return MeetingAnalysis()
        def reduce(self,*args):return MeetingAnalysis()
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    monkeypatch.setattr('app.local_embedding.local_embedding',lambda:SimpleNamespace(release=lambda:None))
    worker.start()
    try:
        assert yielded.wait(2)
        deadline=time.monotonic()+3
        while scheduler.waiting_for(jid) is None and time.monotonic()<deadline:time.sleep(.005)
        assert scheduler.waiting_for(jid)['code']=='realtime_priority'
        assert (store.directory(mid)/jid/'map-0000.c3.json').exists() and calls==['s0']
        assert store.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']!='failed'
        store.execute("UPDATE live_sessions SET status='stopped' WHERE recording_id=?",(rid,));scheduler.set_realtime([])
        deadline=time.monotonic()+3
        while store.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']!='completed' and time.monotonic()<deadline:time.sleep(.01)
        assert store.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']=='completed'
        assert calls==['s0','s1','s2'] and store.one('SELECT count(*) AS n FROM artifacts')['n']==1
    finally:worker.close()
    assert not scheduler.snapshot()['requests']


def test_priority_wait_does_not_block_audio_chunk_persistence(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store);rid=recordings.create('audio/wav')['id']
    scheduler=ResourceScheduler();scheduler.set_realtime([rid]);request=ResourceGate(scheduler).request('analysis',{'local_llm':1})
    assert not request.acquire(False)
    payload=b'\1\0'*1000
    for index in range(20):recordings.chunk(rid,index,payload)
    assert all((recordings.folder(rid)/f'{i:08d}.part').read_bytes()==payload for i in range(20))
    assert recordings.read(rid)['state']=='recording' and not any(scheduler.used.values())
    request.close()


def test_waiting_minutes_status_is_not_model_failure_and_follows_cancellation(tmp_path):
    from app.minutes_generation import MinutesGeneration
    from app.resources import ResourceYielded
    store=Store(tmp_path);mid=uid();jid=uid()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'synthetic','test.wav'))
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'running','minutes','{}')",(jid,mid))
    tracker=MinutesGeneration(store)
    @tracker.track
    def generate(mid,body):raise ResourceYielded()
    with pytest.raises(ResourceYielded):generate(mid,SimpleNamespace(job_id=jid))
    assert tracker.status(mid)['state']=='waiting' and tracker.status(mid)['error']==''
    from fastapi import HTTPException
    with pytest.raises(HTTPException):generate(mid,SimpleNamespace(job_id=None))
    store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(jid,))
    assert tracker.status(mid)['state']=='cancelled'


def test_unknown_native_pause_tail_does_not_pretend_to_be_fully_confirmed(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store);rid=recordings.create('audio/wav')['id']
    store.execute("INSERT INTO live_sessions(recording_id,source,status,config) VALUES(?,'native','running','{}')",(rid,))
    recordings.write(recordings.folder(rid)/'capture.json',{'state':'paused'})
    scheduler=ResourceScheduler();sync_live_demand(scheduler,store);assert scheduler.live_owners=={rid}


def test_running_child_finishes_exit_before_cancel_releases_quota(tmp_path,monkeypatch):
    import sys
    from app.worker import Cancelled
    store=Store(tmp_path);scheduler=ResourceScheduler();worker=Worker(store);worker.resources=scheduler;worker.asr_lock=ResourceGate(scheduler)
    mid=uid();jid=uid();store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'running','analysis','{}')",(jid,mid))
    task=store.one('SELECT * FROM jobs WHERE id=?',(jid,));ready=tmp_path/'ready';exiting=tmp_path/'exiting';exited=tmp_path/'exited'
    script="""import signal,time,sys
from pathlib import Path
def stop(*_):
 Path(sys.argv[2]).touch();time.sleep(.25);Path(sys.argv[3]).touch();raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
Path(sys.argv[1]).touch()
time.sleep(20)
"""
    monkeypatch.setattr('app.local_embedding.local_embedding',lambda:SimpleNamespace(release=lambda:None))
    worker.process_analysis=lambda job:worker.run(jid,[sys.executable,'-c',script,str(ready),str(exiting),str(exited)],tmp_path/'child.log')
    outcomes=[]
    def process():
        try:worker.process(task)
        except Cancelled:outcomes.append('cancelled')
    thread=threading.Thread(target=process);thread.start()
    try:
        deadline=time.monotonic()+2
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.005)
        assert ready.exists();scheduler.set_realtime(['session'])
        assert scheduler.used['legacy_inference']==1 and not exited.exists()
        store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(jid,));assert scheduler.cancel_waiting(jid)==0
        deadline=time.monotonic()+2
        while not exiting.exists() and time.monotonic()<deadline:time.sleep(.005)
        assert exiting.exists() and scheduler.used['legacy_inference']==1
        thread.join(3)
        assert not thread.is_alive() and exited.exists() and outcomes==['cancelled']
        assert not any(scheduler.used.values())
    finally:
        store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(jid,));thread.join(5)


def test_stale_cancel_after_retry_does_not_withdraw_the_new_attempt(tmp_path):
    app=create_app(tmp_path,start_worker=False);store=app.state.store;mid=uid();jid=uid()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'synthetic','test.wav'))
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'cancelled','analysis','{}')",(jid,mid))
    with TestClient(app) as client:
        scheduler=app.state.services.resources
        with scheduler.request('live_overview',{'local_llm':1}):
            assert client.post('/api/jobs/'+jid+'/retry',headers={'X-Task-Attempt':'0'}).status_code==200
            request=app.state.services.live_asr_lock.request('analysis',{'local_llm':1},jid);assert not request.acquire(False)
            assert client.post('/api/jobs/'+jid+'/cancel',headers={'X-Task-Attempt':'0'}).status_code==409
            assert client.post('/api/jobs/'+jid+'/retry',headers={'X-Task-Attempt':'0'}).status_code==409
            assert request.state=='waiting' and store.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']=='queued'
            assert client.post('/api/jobs/'+jid+'/cancel',headers={'X-Task-Attempt':'1'}).status_code==200
            assert request.state=='closed' and scheduler.used['local_llm']==1
            assert client.post('/api/jobs/'+jid+'/cancel',headers={'X-Task-Attempt':'bad'}).status_code==422
