"""C3 fault injection: synthetic phases, process exit and publication replay only."""
import json,os,subprocess,sys,time
from pathlib import Path
import pytest
from app.db import Store,uid,encode
from app.job_checkpoints import JobCheckpoints,atomic_json,digest,file_digest,recovery_view,ResourceInterrupted,validate_segments
from app.worker import Worker
from app.analysis import MeetingAnalysis,AnalysisItem,save_meeting_analysis


def seed(tmp_path):
    store=Store(tmp_path);mid=uid();jid=uid();store.directory(mid).mkdir()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'synthetic','test.wav'))
    segments=[{'id':'s1','start':0,'end':1,'speaker':'甲','text':'周五交付'}]
    revision=store.save_revision(mid,segments,'manual')
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind,config) VALUES(?,?,'running','analysis',?)",(jid,mid,encode({'revision':revision,'model':'synthetic','max_chars':8000})))
    return store,store.one('SELECT * FROM jobs WHERE id=?',(jid,)),segments


def test_checkpoint_commit_survives_new_instance_and_contains_no_configuration(tmp_path):
    store,job,segments=seed(tmp_path);config=json.loads(job['config']);config['api_key']='secret'
    job['config']=encode(config)
    checkpoints=JobCheckpoints(store,job)
    checkpoints.save('map-0000',{'summary':[]},segments)
    assert JobCheckpoints(store,job).load('map-0000',segments)=={'summary':[]}
    assert 'secret' not in checkpoints.path.read_text()
    assert os.stat(checkpoints._path('map-0000')).st_mode & 0o777==0o600
    view=recovery_view(store,job)
    assert view['checkpoints']==1 and view['reused']==1
    assert not any(key in json.dumps(view) for key in ('binding','checksum','payload','secret',str(tmp_path)))


@pytest.mark.parametrize('mutation',['payload','checksum','protocol','input','binding','truncated'])
def test_invalid_checkpoint_is_rejected_without_becoming_a_result(tmp_path,mutation):
    store,job,segments=seed(tmp_path);cache=JobCheckpoints(store,job);cache.save('map-0000',{'summary':[]},segments)
    path=cache._path('map-0000');value=json.loads(path.read_text())
    if mutation=='payload':value['payload']={'summary':['invented']}
    elif mutation=='protocol':value[mutation]=99
    elif mutation!='truncated':value[mutation]='invalid'
    path.write_text('{' if mutation=='truncated' else encode(value))
    assert JobCheckpoints(store,job).load('map-0000',segments) is None
    assert recovery_view(store,job)['checkpoints']==0
    cache=JobCheckpoints(store,job);cache.save('map-0000',{'summary':[]},segments)
    assert cache.load('map-0000',segments)=={'summary':[]}


def test_loaded_payload_revalidates_citations_and_phase_inputs(tmp_path):
    store,job,segments=seed(tmp_path);cache=JobCheckpoints(store,job)
    bad=MeetingAnalysis(summary=[AnalysisItem(content='猜测',source_segment_ids=['missing'])]).model_dump()
    cache.save('map-0000',bad,segments)
    from app.analysis import validate_citations
    def validate(value):
        result=MeetingAnalysis.model_validate(value);validate_citations(result,{'s1'});return result
    assert cache.load('map-0000',segments,validate) is None
    cache.save('map-0000',MeetingAnalysis().model_dump(),segments)
    assert cache.load('map-0000',[dict(segments[0],text='已变化')],validate) is None


@pytest.mark.parametrize('mutation',['config','version','corrupt'])
def test_changed_job_or_unsupported_manifest_refuses_recovery(tmp_path,mutation):
    store,job,_=seed(tmp_path);cache=JobCheckpoints(store,job)
    if mutation=='config':job['config']=encode({'model':'changed'})
    elif mutation=='version':cache.path.write_text(encode({**cache.meta,'protocol':99}))
    else:cache.path.write_text('{')
    with pytest.raises(ValueError,match='检查点'):JobCheckpoints(store,job)


def test_uncommitted_legacy_files_are_not_trusted(tmp_path):
    store,job,segments=seed(tmp_path);cache=JobCheckpoints(store,job)
    (cache.folder/'map-0000.json').write_text(encode(MeetingAnalysis().model_dump()))
    (cache.folder/'.map-0000.c3.json.interrupted.tmp').write_text('partially written')
    assert cache.load('map-0000',segments) is None


def test_failed_atomic_replace_keeps_previous_commit_and_cleans_temp(tmp_path,monkeypatch):
    store,job,segments=seed(tmp_path);cache=JobCheckpoints(store,job);cache.save('map-0000',{'v':1},segments)
    replace=os.replace
    def fail(source,target):
        if str(target).endswith('map-0000.c3.json'):raise OSError('synthetic disk failure')
        return replace(source,target)
    with monkeypatch.context() as context:
        context.setattr('app.job_checkpoints.os.replace',fail)
        with pytest.raises(OSError):cache.save('map-0000',{'v':2},segments)
    assert JobCheckpoints(store,job).load('map-0000',segments)=={'v':1}
    assert not list(cache.folder.glob('.*.tmp'))


def test_phase_commit_before_summary_failure_can_be_recovered(tmp_path,monkeypatch):
    store,job,segments=seed(tmp_path);cache=JobCheckpoints(store,job)
    write=atomic_json
    def fail(path,value):
        if Path(path).name=='recovery.json':raise OSError('synthetic summary write failure')
        return write(path,value)
    with monkeypatch.context() as context:
        context.setattr('app.job_checkpoints.atomic_json',fail)
        with pytest.raises(OSError):cache.save('map-0000',{'v':1},segments)
    fresh=JobCheckpoints(store,job)
    assert fresh.load('map-0000',segments)=={'v':1}
    assert recovery_view(store,job)['checkpoints']==1


def test_binary_receipt_binds_source_and_detects_modified_file(tmp_path):
    store,job,_=seed(tmp_path);cache=JobCheckpoints(store,job);audio=cache.folder/'audio.wav';audio.write_bytes(b'synthetic')
    cache.save_file('audio',audio,{'source':'a'})
    assert cache.file_valid('audio',audio,{'source':'a'})
    assert not cache.file_valid('audio',audio,{'source':'b'})
    audio.write_bytes(b'modified');assert not cache.file_valid('audio',audio,{'source':'a'})


def test_real_process_exit_then_worker_restart_reuses_checkpoint_once(tmp_path,monkeypatch):
    store,job,segments=seed(tmp_path)
    script="""import os,sys,json
from app.db import Store
from app.job_checkpoints import JobCheckpoints
from app.analysis import MeetingAnalysis
store=Store(sys.argv[1]);job=store.one('SELECT * FROM jobs WHERE id=?',(sys.argv[2],))
segments=json.loads(store.one('SELECT segments FROM revisions WHERE meeting_id=? AND revision=1',(job['meeting_id'],))['segments'])
JobCheckpoints(store,job).save('map-0000',MeetingAnalysis().model_dump(),segments)
os._exit(77)
"""
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path),job['id']],env={**os.environ,'PYDANTIC_DISABLE_PLUGINS':'__all__'},timeout=10)
    assert result.returncode==77
    class Fake:
        def models(self):return ['synthetic']
        def analyze_chunk(self,*args):pytest.fail('validated checkpoint should be reused')
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    worker=Worker(store);worker.start()
    try:
        deadline=time.monotonic()+3
        while store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']!='completed' and time.monotonic()<deadline:time.sleep(.01)
        assert store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']=='completed'
        assert store.one("SELECT count(*) AS n FROM job_runtime_events WHERE action='recover'")['n']==1
        worker.process(job) # A stale delivery after database commit must be a no-op.
        assert store.one("SELECT count(*) AS n FROM artifacts WHERE kind='meeting_analysis'")['n']==1
    finally:worker.close()


def test_cancel_between_checkpoint_and_publish_preserves_checkpoint_only(tmp_path,monkeypatch):
    store,job,segments=seed(tmp_path);worker=Worker(store)
    class Fake:
        def models(self):return ['synthetic']
        def analyze_chunk(self,*args):
            store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job['id'],));return MeetingAnalysis()
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    from app.worker import Cancelled
    with pytest.raises(Cancelled):worker.process(job)
    assert not store.all('SELECT * FROM artifacts')
    assert JobCheckpoints(store,job).load('map-0000',segments) is not None


def test_analysis_publication_replay_keeps_artifact_identity(tmp_path):
    store,job,_=seed(tmp_path);provenance={'job':job['id']}
    first=save_meeting_analysis(store,job['meeting_id'],1,MeetingAnalysis(),provenance,job_id=job['id'],finalize_job=False)
    second=save_meeting_analysis(store,job['meeting_id'],1,MeetingAnalysis(),provenance,job_id=job['id'],finalize_job=False)
    assert first==second and len(store.all('SELECT * FROM artifacts'))==1


def test_shutdown_interruption_is_recoverable_and_does_not_look_like_cancel(tmp_path):
    store,job,_=seed(tmp_path);worker=Worker(store);worker.stop.set()
    with pytest.raises(ResourceInterrupted):worker.check(job['id'])
    store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job['id'],))
    from app.worker import Cancelled
    with pytest.raises(Cancelled):worker.check(job['id'])


@pytest.mark.parametrize('bad',[None,{},[{'id':'a','text':'bad','speaker':'甲','start':0,'end':float('nan')}],[{'id':'a','text':'bad','speaker':'甲','start':0,'end':2}]])
def test_invalid_transcription_checkpoint_never_publishes(bad):
    with pytest.raises(ValueError):validate_segments(bad,1)


def test_publication_rollback_then_resume_uses_validated_result(tmp_path,monkeypatch):
    store,job,_=seed(tmp_path);calls=[]
    class Fake:
        def models(self):return ['synthetic']
        def analyze_chunk(self,*args):calls.append('map');return MeetingAnalysis()
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    store.execute("CREATE TRIGGER synthetic_fail_publish BEFORE INSERT ON artifacts BEGIN SELECT RAISE(ABORT,'synthetic publication failure');END")
    worker=Worker(store)
    with pytest.raises(Exception,match='synthetic publication failure'):worker.process(job)
    assert calls==['map'] and not store.all('SELECT * FROM artifacts')
    store.execute('DROP TRIGGER synthetic_fail_publish')
    worker.process(job)
    assert calls==['map'] and len(store.all('SELECT * FROM artifacts'))==1
    assert store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']=='completed'


def test_shutdown_tracks_minutes_as_waiting_and_restart_projection_stays_waiting(tmp_path):
    from types import SimpleNamespace
    from app.minutes_generation import MinutesGeneration
    store,job,_=seed(tmp_path);tracker=MinutesGeneration(store)
    @tracker.track
    def operation(mid,body):raise ResourceInterrupted()
    with pytest.raises(ResourceInterrupted):operation(job['meeting_id'],SimpleNamespace(job_id=job['id']))
    assert tracker.status(job['meeting_id'])['state']=='waiting'
    folder=store.directory(job['meeting_id']);path=folder/'minutes-generation.json';value=json.loads(path.read_text())
    path.write_text(encode({**value,'state':'running'})) # Simulate abrupt process exit before status persistence.
    store.execute("UPDATE jobs SET state='queued' WHERE id=?",(job['id'],))
    assert tracker.status(job['meeting_id'])['state']=='waiting'
    store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job['id'],))
    assert tracker.status(job['meeting_id'])['state']=='cancelled'


def test_completed_publication_is_authoritative_even_if_worker_acknowledgment_fails(tmp_path,monkeypatch):
    store,job,_=seed(tmp_path);store.execute("UPDATE jobs SET state='queued' WHERE id=?",(job['id'],))
    worker=Worker(store)
    class Fake:
        def models(self):return ['synthetic']
        def analyze_chunk(self,*args):return MeetingAnalysis()
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    process=worker.process_analysis
    def lost_ack(job):process(job);raise RuntimeError('synthetic lost acknowledgment')
    monkeypatch.setattr(worker,'process_analysis',lost_ack)
    worker.start()
    try:
        deadline=time.monotonic()+3
        while store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']!='completed' and time.monotonic()<deadline:time.sleep(.01)
        assert store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']=='completed'
        worker.process(job)
        assert len(store.all('SELECT * FROM artifacts'))==1
    finally:worker.close()


def test_recovery_metadata_changes_block_retry_without_mutating_files(tmp_path):
    from app.task_management import retry_blocker
    store,job,_=seed(tmp_path);cache=JobCheckpoints(store,job);before=cache.path.read_bytes()
    job['state']='failed';job['config']=encode({'model':'another'})
    assert '检查点' in retry_blocker(store,job,Worker(store))
    assert cache.path.read_bytes()==before
    assert recovery_view(store,job)['protocol'] is None
    job['config']='{private malformed configuration'
    assert recovery_view(store,job)['protocol'] is None
    assert '配置不完整' in retry_blocker(store,job,Worker(store))


def test_reduce_checkpoint_resume_after_publication_failure_does_not_repeat_models(tmp_path,monkeypatch):
    store,job,segments=seed(tmp_path);mid=job['meeting_id']
    expanded=[dict(segments[0],id=f's{i}',text='合成分块文本'*30,start=i,end=i+1) for i in range(3)]
    store.execute('UPDATE revisions SET segments=? WHERE meeting_id=? AND revision=1',(encode(expanded),mid))
    config=json.loads(job['config']);config['max_chars']=100;job['config']=encode(config)
    store.execute('UPDATE jobs SET config=? WHERE id=?',(job['config'],job['id']))
    calls=[]
    class Fake:
        def models(self):return ['synthetic']
        def analyze_chunk(self,*args):calls.append('map');return MeetingAnalysis()
        def reduce(self,*args):calls.append('reduce');return MeetingAnalysis()
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    store.execute("CREATE TRIGGER synthetic_fail_publish BEFORE INSERT ON artifacts BEGIN SELECT RAISE(ABORT,'synthetic publish');END")
    worker=Worker(store)
    with pytest.raises(Exception,match='synthetic publish'):worker.process(job)
    assert calls==['map','map','map','reduce']
    store.execute('DROP TRIGGER synthetic_fail_publish');worker.process(job)
    assert calls==['map','map','map','reduce']
    assert len(store.all('SELECT * FROM artifacts'))==1
