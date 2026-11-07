"""C4 durable handoff and nonblocking admission; synthetic models/data only."""
import json,time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Store,uid,encode
from app.analysis import AIMinutesResult,AIMinutesBasicInfo,AnalysisItem,MeetingAnalysis
from app.worker import Worker,DispatchDeferred,Cancelled
from app.resources import ResourceScheduler,ResourceGate

@pytest.fixture
def setup(tmp_path,monkeypatch):
    calls=[];hooks={}
    class Fake:
        base_url='http://localhost:1234/v1'
        def resolve_model(self,model=None):return model or 'synthetic'
        def models(self):return ['synthetic']
        def generate_ai_minutes(self,model,segments,context,work,run):
            calls.append(('generate',segments,context))
            if hooks.get('generate'):hooks['generate']()
            if 'result' in hooks:return hooks['result']
            return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成会',source_segment_ids=['s1']),core_content=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])])
        def polish_minutes(self,*args):
            calls.append(('polish',))
            return MeetingAnalysis(key_points=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])])
    monkeypatch.setattr('app.main.LMStudioProvider',Fake)
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid();store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成会','test.wav'))
        store.save_revision(mid,[{'id':'s1','start':0,'end':1,'speaker':'甲','text':'周五交付'}],'manual')
        yield client,store,mid,calls,hooks

def enqueue(setup,**values):
    client,store,mid,_,_=setup
    response=client.post(f'/api/meetings/{mid}/minutes/jobs',json={'model':'synthetic',**values})
    assert response.status_code==202,response.text
    return response.json()['job_id']

def execute(setup,jid):
    client,store,mid,_,_=setup
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    return store.one("SELECT * FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes' AND json_extract(provenance,'$.job')=?",(mid,jid))

def test_submission_is_model_free_durable_and_duplicate_safe(setup):
    c,s,m,calls,_=setup;jid=enqueue(setup)
    assert calls==[]
    again=c.post(f'/api/meetings/{m}/minutes/jobs',json={'model':'synthetic'}).json()
    assert again['job_id']==jid and again['reused']
    assert s.one('SELECT count(*) AS n FROM jobs')['n']==1
    status=c.get(f'/api/meetings/{m}').json()['minutes_generation']
    assert status['state']=='waiting' and status['job_id']==jid
    assert c.post(f'/api/meetings/{m}/minutes/jobs',json={'model':'synthetic','detail_level':'brief'}).status_code==409
    artifact=execute(setup,jid)
    assert artifact and len(calls)==1
    c.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert len(calls)==1 and s.one('SELECT count(*) AS n FROM artifacts')['n']==1

@pytest.mark.parametrize('suffix',['ai','compose'])
def test_explicit_legacy_async_handoff_returns_202_without_inference(setup,suffix):
    c,s,m,calls,_=setup
    response=c.post(f'/api/meetings/{m}/minutes/{suffix}',json={'model':'synthetic'},headers={'Prefer':'respond-async'})
    assert response.status_code==202 and response.headers['location']=='/api/runtime/tasks'
    assert response.json()['job_id'] and calls==[]
    assert c.post(f'/api/meetings/{m}/minutes/ai',json={'model':'synthetic'}).status_code==409


def test_cancel_then_retry_uses_validated_result_and_no_duplicate_output(setup):
    c,s,m,calls,hooks=setup;jid=enqueue(setup)
    hooks['generate']=lambda:s.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(jid,))
    with pytest.raises(Cancelled):execute(setup,jid)
    assert not s.all('SELECT * FROM artifacts')
    hooks.clear();assert c.post('/api/jobs/'+jid+'/retry').status_code==200
    assert execute(setup,jid) and len(calls)==1

@pytest.mark.parametrize('when',['before','during'])
def test_changed_source_blocks_publish_and_before_case_never_infers(setup,when):
    c,s,m,calls,hooks=setup;jid=enqueue(setup)
    change=lambda:s.execute('UPDATE meetings SET title=? WHERE id=?',('变化',m))
    if when=='before':change()
    else:hooks['generate']=change
    with pytest.raises(ValueError,match='变化'):execute(setup,jid)
    assert len(calls)==(0 if when=='before' else 1) and not s.all('SELECT * FROM artifacts')


def test_cloud_dispatch_requires_current_consent_and_bound_destination(setup,monkeypatch):
    c,s,m,calls,_=setup;pid=uid()
    s.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,'cloud',?,?,?)",(pid,'synthetic cloud','https://fixture.invalid/v1','synthetic','private-key'))
    body={'profile_id':pid,'model':'synthetic'}
    assert c.post(f'/api/meetings/{m}/minutes/jobs',json=body).status_code==409
    expected={'id':pid,'base_url':'https://fixture.invalid/v1','provider_type':'cloud','model':'synthetic'}
    response=c.post(f'/api/meetings/{m}/minutes/jobs',json={**body,'confirm_cloud':True,'expected_provider':expected})
    assert response.status_code==202 and calls==[]
    jid=response.json()['job_id'];job=s.one('SELECT * FROM jobs WHERE id=?',(jid,))
    assert 'private-key' not in job['config']
    s.execute('UPDATE llm_configs SET base_url=? WHERE id=?',('https://changed.invalid/v1',pid))
    with pytest.raises(ValueError,match='变化'):execute(setup,jid)
    assert not s.all('SELECT * FROM artifacts')


def test_durable_optional_analysis_is_enqueued_in_publication_transaction(setup):
    c,s,m,calls,_=setup;jid=enqueue(setup,also_analyze=True)
    assert not s.all("SELECT * FROM jobs WHERE kind='analysis'")
    assert execute(setup,jid)
    jobs=s.all("SELECT * FROM jobs WHERE kind='analysis'");assert len(jobs)==1 and jobs[0]['state']=='queued'
    assert json.loads(jobs[0]['config'])['revision']==1
    c.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert len(s.all("SELECT * FROM jobs WHERE kind='analysis'"))==1


def test_current_variant_preserves_manual_fields_and_active_version(setup):
    c,s,m,calls,_=setup;first=execute(setup,enqueue(setup));document=json.loads(first['payload'])
    document['purpose']='人工目的';document['summary']=[{'content':'人工补充','source_segment_ids':[]}]
    response=c.put(f'/api/meetings/{m}/minutes',json={'document':document,'source_minutes_id':first['id'],'allow_branch':True})
    assert response.status_code==201,response.text
    source=response.json()['id'];jid=enqueue(setup,method='current',source_minutes_id=source,detail_level='brief')
    result=execute(setup,jid);payload=json.loads(result['payload'])
    assert payload['purpose']=='人工目的' and payload['summary'][-1]['content']=='人工补充'
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']!=result['id']


def test_polish_handoff_uses_checkpoint_and_remains_a_separate_draft(setup):
    c,s,m,calls,_=setup;first=execute(setup,enqueue(setup))
    response=c.post(f'/api/meetings/{m}/minutes/polish',json={'model':'synthetic'},headers={'Prefer':'respond-async'})
    assert response.status_code==202,response.text
    second=execute(setup,response.json()['job_id'])
    assert second['id']!=first['id'] and json.loads(second['provenance'])['llm_polished']
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==first['id']


def task(store,provider='local'):
    mid=uid();jid=uid();store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'synthetic','test.wav'))
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'queued','analysis',?)",(jid,mid,encode({'provider':{'provider_type':provider}})))
    return store.one('SELECT * FROM jobs WHERE id=?',(jid,))


def test_cloud_job_bypasses_blocked_local_queue_without_releasing_resident_asr(tmp_path,monkeypatch):
    store=Store(tmp_path);scheduler=ResourceScheduler();worker=Worker(store);worker.resources=scheduler;worker.asr_lock=ResourceGate(scheduler)
    local=task(store);cloud=task(store,'cloud');rid=uid()
    store.execute("INSERT INTO live_sessions(recording_id,source,status,config) VALUES(?,'browser','running','{}')",(rid,))
    calls=[];worker.process_analysis=lambda job:calls.append(job['id'])
    worker.release_model=lambda:pytest.fail('cloud job must not unload the live ASR model')
    monkeypatch.setattr('app.local_embedding.local_embedding',lambda:SimpleNamespace(release=lambda:pytest.fail('cloud job must not unload local models')))
    with worker.asr_lock.request():
        worker.start()
        try:
            deadline=time.monotonic()+3
            while store.one('SELECT state FROM jobs WHERE id=?',(cloud['id'],))['state']!='completed' and time.monotonic()<deadline:time.sleep(.01)
            assert calls==[cloud['id']]
            assert store.one('SELECT state FROM jobs WHERE id=?',(local['id'],))['state']=='queued'
            assert scheduler.waiting_for(local['id'])['code']=='realtime_priority'
            assert scheduler.used['legacy_inference']==1 and scheduler.used['cloud_llm']==0
        finally:worker.close()


def test_dispatch_capacity_race_returns_to_queue_without_blocking(tmp_path):
    store=Store(tmp_path);scheduler=ResourceScheduler();worker=Worker(store);worker.resources=scheduler;worker.asr_lock=ResourceGate(scheduler)
    job=task(store);assert worker.next_job()['id']==job['id']
    worker.nonblocking_dispatch=True
    with scheduler.request('live_overview',{'local_llm':1}):
        start=time.monotonic()
        with pytest.raises(DispatchDeferred):worker.process(job)
        assert time.monotonic()-start<.2 and scheduler.used['local_llm']==1
    assert not any(scheduler.used.values())


def test_same_priority_jobs_keep_fifo_while_blocked_requests_are_observed(tmp_path):
    store=Store(tmp_path);scheduler=ResourceScheduler();worker=Worker(store);worker.resources=scheduler
    first=task(store,'cloud');second=task(store,'cloud');local=task(store)
    with scheduler.request('live_overview',{'local_llm':1}):
        assert worker.next_job()['id']==first['id']
        store.execute("UPDATE jobs SET state='completed' WHERE id=?",(first['id'],))
        assert worker.next_job()['id']==second['id']
        store.execute("UPDATE jobs SET state='completed' WHERE id=?",(second['id'],))
        assert worker.next_job() is None
        assert scheduler.waiting_for(local['id'])['code']=='local_llm_busy'
        scheduler.cancel_waiting(local['id']);assert scheduler.waiting_for(local['id']) is None


def test_full_regeneration_allows_stale_parent_but_does_not_reuse_its_contents(setup):
    c,s,m,calls,_=setup;first=execute(setup,enqueue(setup))
    s.save_revision(m,[{'id':'s1','start':0,'end':1,'speaker':'甲','text':'改为周一交付'}],'manual')
    assert execute(setup,enqueue(setup,source_minutes_id=first['id']))
    assert calls[-1][1][0]['text']=='改为周一交付'
    assert 'source_minutes' not in calls[-1][2]


def test_queued_task_survives_app_recreation_and_preserves_attempt_guard(setup):
    c,s,m,calls,_=setup;jid=enqueue(setup)
    # Read via a fresh app object without starting a second service on the live directory.
    restored=TestClient(create_app(s.root,start_worker=False))
    try:
        assert restored.get(f'/api/meetings/{m}').json()['minutes_generation']['job_id']==jid
        assert restored.post('/api/jobs/'+jid+'/cancel',headers={'X-Task-Attempt':'0'}).status_code==200
        assert restored.post('/api/jobs/'+jid+'/retry',headers={'X-Task-Attempt':'0'}).status_code==200
        data=restored.get(f'/api/meetings/{m}').json()
        assert data['jobs'][0]['recorded_retries']==1
        assert restored.post('/api/jobs/'+jid+'/cancel',headers={'X-Task-Attempt':'0'}).status_code==409
    finally:restored.close()
    assert calls==[]


def test_polish_from_rule_minutes_keeps_selected_template(setup):
    c,s,m,calls,_=setup
    from app.minutes_composition import publish_analysis,CompositionSettings
    aid=uid();s.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,1,'meeting_analysis',?,'{}')",(aid,m,encode(MeetingAnalysis(key_points=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])]).model_dump())))
    first=publish_analysis(s,m,1,MeetingAnalysis(key_points=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])]),aid,CompositionSettings(method='analysis'))
    assert execute(setup,enqueue(setup,operation='polish'))
    assert calls[-1][0]=='polish'


def test_runtime_positions_describe_eligible_work_and_probe_does_not_reserve(setup):
    c,s,m,_,_=setup;worker=c.app.state.worker;scheduler=worker.resources
    local=task(s);cloud=task(s,'cloud')
    with scheduler.request('live_overview',{'local_llm':1}):
        before=scheduler.used.copy()
        response=c.get('/api/runtime/tasks').json()['items'];items={row['id']:row for row in response}
        assert items[local['id']]['queue_position'] is None
        assert items[local['id']]['resource_wait']['code']=='local_llm_busy'
        assert items[cloud['id']]['queue_position']==1
        assert scheduler.used==before


def test_job_cancellation_does_not_withdraw_a_live_observation_with_same_owner():
    scheduler=ResourceScheduler()
    with scheduler.request('analysis',{'legacy_inference':1}):
        scheduler.observe_wait('same-owner','live_asr',{'legacy_inference':1,'asr':1})
        scheduler.cancel_waiting('same-owner',{'analysis','minutes'})
        assert scheduler.waiting_for('same-owner')['code']=='legacy_inference_busy'


def test_confirmed_cloud_minutes_publish_while_live_asr_holds_local_channel(setup,monkeypatch):
    c,s,m,calls,_=setup;pid=uid();scheduler=c.app.state.services.resources
    s.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,'cloud',?,?,?)",(pid,'合成云端','https://fixture.invalid/v1','synthetic','synthetic-key'))
    expected={'id':pid,'base_url':'https://fixture.invalid/v1','provider_type':'cloud','model':'synthetic'}
    jid=enqueue(setup,profile_id=pid,confirm_cloud=True,expected_provider=expected)
    def fake(*args,**kwargs):return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成云端',source_segment_ids=['s1']),core_content=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])])
    monkeypatch.setattr('app.minutes_jobs.OpenAICompatibleProvider.generate_ai_minutes',fake)
    c.app.state.worker.release_model=lambda:pytest.fail('must not unload live ASR')
    with c.app.state.services.live_asr_lock.request():
        assert execute(setup,jid)
        assert scheduler.used['legacy_inference']==1 and scheduler.used['cloud_llm']==0
    assert not any(scheduler.used.values())


def test_polish_never_overwrites_a_newly_adopted_version(setup,monkeypatch):
    c,s,m,calls,_=setup;first=execute(setup,enqueue(setup));second=execute(setup,enqueue(setup,detail_level='brief'))
    jid=enqueue(setup,operation='polish')
    def change_selection(*args):
        from app import minutes_versions
        with s.connect() as db:minutes_versions.adopt(db,m,second['id'])
        return MeetingAnalysis(key_points=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])])
    monkeypatch.setattr('app.main.LMStudioProvider.polish_minutes',change_selection)
    with pytest.raises(ValueError,match='采用稿已变化'):execute(setup,jid)
    assert len(s.all("SELECT * FROM artifacts WHERE kind='meeting_minutes'"))==2
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==second['id']

@pytest.mark.parametrize('template,section',[
    ('general-meeting','decisions'),('project-weekly','risks'),
    ('customer-interview','opportunities'),('online-interview','qa'),
])
def test_chinese_record_minutes_preserve_original_sources(setup,template,section,monkeypatch):
    from app.chinese_record import provider_for,translation_path,write_translation
    from app.configuration_queries import ConfigurationQueries
    c,s,m,calls,hooks=setup;pid=uid()
    s.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,'local',?,?)",
              (pid,'translator','http://localhost:1234/v1','translator'))
    original={'id':'s1','start':0,'end':1,'speaker':'甲','text':'Delivery on Friday'}
    missing={'id':'s2','start':1,'end':2,'speaker':'乙','text':'Budget still open'}
    s.save_revision(m,[original,missing],encode({'language':'en'}))
    services=SimpleNamespace(store=s,configurations=ConfigurationQueries(s))
    write_translation(translation_path(services,m,original,'en',provider_for(services,pid)),'周五交付')
    if template=='online-interview':
        from app.interview import InterviewResult
        result=AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成面试'),interview=InterviewResult(qa=[
            {'question':'何时交付？','content':'周五','evidence':[{'segment_id':'s1','quote':'Delivery on Friday'}]}]))
        # Supply a structured result from the same fake inference path.
        hooks['result']=result
    from app.analysis import OpenAICompatibleProvider
    from app.main import LMStudioProvider
    monkeypatch.setattr(OpenAICompatibleProvider,"generate_ai_minutes",LMStudioProvider.generate_ai_minutes)
    artifact=execute(setup,enqueue(setup,template_id=template,chinese_record_profile_id=pid))
    document=json.loads(artifact['payload'])
    assert document['template_id']==template
    assert section in {item['id'] for item in document['template_snapshot']['sections']}
    assert document.get('interviewer_evaluation','')==''
    assert calls[0][1]==[{**original,'translation_zh':'周五交付'},missing]
    assert json.loads(artifact['provenance'])['chinese_record']['counts']=={'original':0,'translated':1,'pending':1,'unknown':0}
