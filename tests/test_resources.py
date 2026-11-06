"""C1 fault injection: no devices/models; resource waits must not hold storage locks."""
import json,threading,time
import pytest
from fastapi.testclient import TestClient
from app.resources import ResourceScheduler,ResourceGate,ResourceBusy,optional_slot,request_for
from app.main import create_app
from app.db import Store,uid
from app.worker import Worker,Cancelled
from app.recordings import RecordingStore


def test_atomic_bundles_do_not_occupy_legacy_gate_while_waiting_for_llm():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler)
    with scheduler.request('live_overview',{'local_llm':1}):
        request=gate.request('analysis',{'local_llm':1},'private-owner')
        assert not request.acquire(False)
        assert scheduler.used['legacy_inference']==0
        assert scheduler.waiting_for('private-owner')['code']=='local_llm_busy'
        assert gate.acquire(False);gate.release()
        request.close()
    assert not any(scheduler.used.values())


def test_timeout_retry_keeps_same_ticket_and_failed_context_releases_all_slots():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler)
    with gate.request('transcription',{'asr':1}):
        waiting=gate.request('index',{'embedding':1})
        assert not waiting.acquire(timeout=.01);ticket=waiting.id
        assert not waiting.acquire(timeout=.01) and waiting.id==ticket
        assert len(scheduler.snapshot()['requests'])==2
    with waiting:
        assert scheduler.used['embedding']==1
    with pytest.raises(RuntimeError):
        with gate.request('analysis',{'local_llm':1}):raise RuntimeError('private-error must not be recorded')
    assert not any(scheduler.used.values())
    assert 'private-error' not in json.dumps(scheduler.snapshot())
    assert scheduler.snapshot()['history'][-1]['outcome']=='failed'


def test_blocking_wait_is_notified_and_closed_coordinator_preserves_active_lease():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler);holder=gate.request();holder.acquire()
    request=gate.request('index',{'embedding':1});started=threading.Event();outcomes=[]
    def wait():
        started.set()
        try:request.acquire(timeout=2)
        except ResourceBusy:outcomes.append('closed')
        finally:request.close()
    thread=threading.Thread(target=wait);thread.start();assert started.wait(1)
    scheduler.close();thread.join(2)
    assert not thread.is_alive() and outcomes==['closed']
    assert scheduler.used['legacy_inference']==1
    holder.close();assert not any(scheduler.used.values())


def test_resource_limit_and_history_are_bounded_and_owner_data_is_private():
    scheduler=ResourceScheduler(max_requests=2,history_limit=3)
    with scheduler.request('analysis',{'local_llm':1},'PRIVATE-KEY'):
        pending=scheduler.request('minutes',{'local_llm':1},'PRIVATE-TITLE');assert not pending.acquire(False)
        extra=scheduler.request('minutes',{'local_llm':1})
        with pytest.raises(ResourceBusy):extra.acquire(False)
        extra.close();pending.close()
    for _ in range(8):
        with scheduler.request('live_asr',{'asr':1}):pass
    scheduler.set_index_deferred(['private-'+str(i) for i in range(20)])
    snapshot=scheduler.snapshot()
    assert len(snapshot['history'])==3 and len(snapshot['requests'])==2
    assert snapshot['deferred_total']==20 and snapshot['deferred_truncated']
    assert 'PRIVATE' not in json.dumps(snapshot) and 'private-' not in json.dumps(snapshot)
    assert snapshot['rejected_count']==1


@pytest.mark.parametrize('spec',[{}, {'asr':0},{'asr':True},{'asr':2},{'unknown':1}])
def test_invalid_units_or_pools_cannot_change_usage(spec):
    scheduler=ResourceScheduler()
    with pytest.raises(ValueError):scheduler.request('live_asr',spec)
    assert not scheduler.snapshot()['requests'] and not any(scheduler.used.values())


def test_lock_compatibility_double_release_and_plain_lock_fallback():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler)
    assert gate.acquire(False) and gate.locked()
    assert not gate.acquire(False)
    gate.release();assert not gate.locked()
    with pytest.raises(RuntimeError):gate.release()
    lock=threading.Lock();claim=request_for(lock,'transcription',{'asr':1})
    assert claim.acquire(False) and lock.locked()
    claim.close();claim.close();assert not lock.locked()
    with pytest.raises(ValueError):ResourceGate(ResourceScheduler({'legacy_inference':2}))


def test_observed_asr_waits_do_not_reserve_quota_and_clear_without_inference():
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler)
    with gate.request('analysis',{'local_llm':1}):
        gate.observe_wait('private-rid');first=scheduler.snapshot()['requests'][-1]['id']
        gate.observe_wait('private-rid');last=scheduler.snapshot()['requests'][-1]
        assert last['id']==first and last['state']=='deferred'
        assert scheduler.used['asr']==0
        gate.clear_wait('private-rid');assert len(scheduler.snapshot()['requests'])==1
    assert not scheduler.snapshot()['requests']


def job(store,kind='analysis',config=None):
    jid=uid();store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'running',?,?)",(jid,uid(),kind,json.dumps(config or {})))
    return store.one('SELECT * FROM jobs WHERE id=?',(jid,))


def test_cancelled_worker_wait_releases_request_without_running_models(tmp_path,monkeypatch):
    store=Store(tmp_path);scheduler=ResourceScheduler();worker=Worker(store);worker.asr_lock=ResourceGate(scheduler);worker.resources=scheduler
    task=job(store);called=[];worker.process_analysis=lambda _:called.append('MODEL MUST NOT RUN')
    errors=[]
    def process():
        try:worker.process(task)
        except Cancelled:errors.append('cancelled')
    with scheduler.request('live_overview',{'local_llm':1}):
        thread=threading.Thread(target=process);thread.start()
        deadline=time.monotonic()+2
        while scheduler.waiting_for(task['id']) is None and time.monotonic()<deadline:time.sleep(.005)
        assert scheduler.waiting_for(task['id'])['code']=='local_llm_busy'
        store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(task['id'],));thread.join(2)
        assert not thread.is_alive() and errors==['cancelled'] and not called
        assert len(scheduler.snapshot()['requests'])==1
    assert not scheduler.snapshot()['requests']


def test_resource_starvation_does_not_block_continuous_audio_chunk_persistence(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store);session=recordings.create('audio/wav');rid=session['id']
    scheduler=ResourceScheduler();gate=ResourceGate(scheduler);pending=gate.request('index',{'embedding':1})
    written=[];errors=[]
    def capture():
        try:
            for index in range(30):
                recordings.chunk(rid,index,b'\1\0'*1600);written.append(index);time.sleep(.002)
        except BaseException as error:errors.append(type(error).__name__)
    with gate.request('transcription',{'asr':1}):
        assert not pending.acquire(False)
        writer=threading.Thread(target=capture);writer.start();writer.join(2)
        assert not writer.is_alive() and not errors and written==list(range(30))
        assert recordings.read(rid)['state']=='recording'
        folder=recordings.folder(rid)
        parts=list(folder.glob('*.part'))
        assert len(parts)==30 and all(part.read_bytes()==b'\1\0'*1600 for part in parts)
        assert scheduler.used['legacy_inference']==1 and scheduler.used['embedding']==0
    pending.close();assert not any(scheduler.used.values())


def test_index_yield_is_observation_only_and_resumes_existing_queue_rule(tmp_path):
    store=Store(tmp_path);worker=Worker(store);worker.resources=ResourceScheduler()
    task=job(store,'retrieval_index');store.execute("UPDATE jobs SET state='queued' WHERE id=?",(task['id'],))
    rid=uid();store.execute("INSERT INTO live_sessions(recording_id,source,config,status) VALUES(?,'browser','{}','running')",(rid,))
    assert worker.next_job() is None
    assert worker.resources.waiting_for(task['id'])['code']=='realtime_index_yield'
    assert not any(worker.resources.used.values())
    store.execute("UPDATE live_sessions SET status='stopped' WHERE recording_id=?",(rid,))
    assert worker.next_job()['id']==task['id'] and not worker.resources.snapshot()['requests']


def test_cloud_qa_uses_saved_provider_and_does_not_reserve_local_gpu_slots(tmp_path):
    store=Store(tmp_path);worker=Worker(store);qid=uid()
    store.execute("INSERT INTO qa_runs(id,config) VALUES(?,?)",(qid,json.dumps({'provider':{'provider_type':'cloud'}})))
    workload,requirements=worker.resource_spec(job(store,'retrieval_answer',{'qa_id':qid}))
    assert workload=='qa' and requirements=={'cloud_llm':1}


def test_runtime_query_is_passive_private_and_application_scoped(tmp_path,monkeypatch):
    left=create_app(tmp_path/'left',start_worker=False);right=create_app(tmp_path/'right',start_worker=False)
    left.state.services.resident.warm=lambda *_:pytest.fail('status must not warm model')
    left.state.services.native_capture.start=lambda *_:pytest.fail('status must not start capture')
    with TestClient(left) as a,TestClient(right) as b:
        scheduler=left.state.services.resources
        with scheduler.request('analysis',{'local_llm':1},'SECRET-PROMPT'):
            response=a.get('/api/runtime/resources');data=response.json()
            assert response.headers['cache-control']=='no-store'
            assert data['requests'][0]['workload']=='analysis'
            assert b.get('/api/runtime/resources').json()['requests']==[]
            assert 'SECRET' not in response.text
            diagnostic=a.get('/api/runtime/diagnostics')
            assert diagnostic.json()['resource_runtime']['requests'][0]['workload']=='analysis'
            assert 'SECRET' not in diagnostic.text
            assert a.get('/api/runtime/resources',headers={'origin':'https://remote.test'}).status_code==403
    assert not scheduler.snapshot()['requests'] and scheduler.closed


def test_parallel_admission_never_exceeds_capacity():
    scheduler=ResourceScheduler({'local_llm':2});mutex=threading.Lock();inside=0;peak=0;failures=[]
    def work():
        nonlocal inside,peak
        try:
            for _ in range(10):
                with scheduler.request('analysis',{'local_llm':1}):
                    with mutex:inside+=1;peak=max(peak,inside)
                    time.sleep(.001)
                    with mutex:inside-=1
        except BaseException as error:failures.append(type(error).__name__)
    threads=[threading.Thread(target=work) for _ in range(8)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(3)
    assert not any(t.is_alive() for t in threads) and not failures
    assert peak==2 and inside==0 and scheduler.used['local_llm']==0
    assert scheduler.finished==80 and len(scheduler.snapshot()['history'])==50


def test_worker_failure_does_not_leave_compute_reserved(tmp_path,monkeypatch):
    from types import SimpleNamespace
    store=Store(tmp_path);worker=Worker(store);scheduler=ResourceScheduler();worker.resources=scheduler;worker.asr_lock=ResourceGate(scheduler)
    monkeypatch.setattr('app.local_embedding.local_embedding',lambda:SimpleNamespace(release=lambda:None))
    def fail(_):raise RuntimeError('synthetic inference failure')
    worker.process_analysis=fail
    with pytest.raises(RuntimeError):worker.process(job(store))
    assert not scheduler.snapshot()['requests'] and not any(scheduler.used.values())


@pytest.mark.parametrize('timeout',[float('nan'),float('inf'),-2])
def test_invalid_timeout_is_rejected_before_registration(timeout):
    scheduler=ResourceScheduler();claim=scheduler.request('analysis',{'local_llm':1})
    with pytest.raises(ValueError):claim.acquire(timeout=timeout)
    claim.close();assert not scheduler.snapshot()['requests']


def test_withdrawn_waiter_never_enters_context_without_lease():
    scheduler=ResourceScheduler();holder=scheduler.request('minutes',{'local_llm':1});holder.acquire()
    claim=scheduler.request('analysis',{'local_llm':1});entered=[];errors=[]
    def enter():
        try:
            with claim:entered.append('must not start')
        except ResourceBusy:errors.append('withdrawn')
    thread=threading.Thread(target=enter);thread.start()
    deadline=time.monotonic()+1
    while claim.state!='waiting' and time.monotonic()<deadline:time.sleep(.002)
    claim.close();thread.join(1);holder.close()
    assert not thread.is_alive() and not entered and errors==['withdrawn']
    assert not scheduler.snapshot()['requests']
