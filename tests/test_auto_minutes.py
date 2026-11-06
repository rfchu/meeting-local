import io,json,wave
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.recordings import RecordingStore
from app.analysis import AIMinutesResult,AIMinutesBasicInfo,AnalysisItem
from app.auto_minutes import enqueue_after_transcription
from app.worker import Cancelled

@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False);calls=[];hooks={}
    class ASR:
        engine_id='whisper';label='合成 ASR';model='synthetic.bin';needs_punctuation=False;chunk_seconds=300
        def ready(self):return True
        def identity(self):return {}
    class LLM:
        def __init__(self,profile):self.profile=profile;self.base_url=profile['base_url']
        def resolve_model(self,model=None):return model or self.profile['model']
        def generate_ai_minutes(self,model,segments,metadata,work,run):
            calls.append(model)
            if hooks.get('generate'):hooks['generate']()
            return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成会议',source_segment_ids=['s1']),core_content=[AnalysisItem(content='周五交付',source_segment_ids=['s1'])],action_items=[AnalysisItem(content='提交方案',source_segment_ids=['s1'],owner='甲',due_date='周五')],summary=[AnalysisItem(content='确认安排',source_segment_ids=['s1'])])
    monkeypatch.setattr('app.main.get_asr_provider',lambda *_:ASR())
    monkeypatch.setattr('app.worker.get_asr_provider',lambda *_:ASR())
    monkeypatch.setattr('app.auto_minutes.OpenAICompatibleProvider',LLM)
    with TestClient(app) as client:
        store=app.state.store;session=RecordingStore(store).create('audio/wav');mid=session['id'];folder=store.directory(mid);folder.mkdir()
        audio=io.BytesIO()
        with wave.open(audio,'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*48000)
        (folder/'original').write_bytes(audio.getvalue())
        store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,'合成会议','test.wav',3))
        pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,?,?,?,?)',(pid,'合成模型','local','http://127.0.0.1:1234/v1','synthetic-model','secret-not-in-job'))
        def fast(*args):
            if hooks.get('asr'):hooks['asr']()
            return [{'id':'s1','speaker':'甲','start':0,'end':3,'text':'周五由甲提交方案。'}],{}
        monkeypatch.setattr(app.state.worker,'fast_transcription',fast)
        yield client,store,mid,pid,calls,hooks,audio.getvalue()

def enqueue(setup,confirm=False):
    c,s,m,p,_,_,_=setup
    profile=s.one('SELECT * FROM llm_configs WHERE id=?',(p,))
    expected={k:profile[k] for k in ('id','base_url','provider_type','model')}
    response=c.post(f'/api/meetings/{m}/jobs',json={'engine':'whisper','completion_mode':'quick','minutes_followup':{'profile_id':p,'confirm_cloud':confirm,'expected_provider':expected,'settings':{'template_id':'project-weekly','detail_level':'brief'}}})
    assert response.status_code==201,response.text
    return response.json()['id']

def transcribe(setup,jid):
    c,s,m,_,_,_,audio=setup
    folder=s.directory(m)/jid;folder.mkdir();(folder/'audio.wav').write_bytes(audio)
    s.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    c.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    return s.one("SELECT * FROM jobs WHERE meeting_id=? AND kind='minutes'",(m,))

def generate(setup,job,client=None):
    c,s,*_=setup;c=client or c
    s.execute("UPDATE jobs SET state='running' WHERE id=?",(job['id'],))
    c.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(job['id'],)))
    return c.get(f'/api/meetings/{job["meeting_id"]}').json()

def test_transcript_and_child_queue_commit_together_and_survive_restart(setup):
    c,s,m,_,calls,_,_=setup;jid=enqueue(setup);job=transcribe(setup,jid)
    assert job and s.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']=='completed'
    assert json.loads(job['config'])['revision']==1
    assert 'secret-not-in-job' not in job['config']
    c.__exit__(None,None,None)  # Release the previous service before a real restart.
    with TestClient(create_app(s.root,start_worker=False)) as restarted:detail=generate(setup,job,restarted)
    artifact=next(a for a in detail['artifacts'] if a['kind']=='meeting_minutes')
    assert artifact['revision']==1 and artifact['provenance']['automatic']
    assert artifact['payload']['template_id']=='project-weekly'
    assert detail['active_minutes_id']==artifact['id'] and calls==['synthetic-model']
    assert detail['minutes_generation']['state']=='completed'

def test_existing_adopted_minutes_are_preserved_as_new_automatic_draft(setup):
    from app.minutes import MeetingMinutes
    c,s,m,_,_,_,_=setup
    jid=enqueue(setup);job=transcribe(setup,jid)
    old=uid();s.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,1,'meeting_minutes',?,'{}')",(old,m,encode(MeetingMinutes(title='已采用稿',style='ai').model_dump())))
    detail=generate(setup,job)
    assert detail['active_minutes_id']==old
    assert next(a for a in detail['artifacts'] if a['id']!=old)['provenance']['draft']

def test_cloud_consent_required_before_any_asr_job_is_created(setup):
    c,s,m,p,_,_,_=setup;s.execute("UPDATE llm_configs SET provider_type='cloud',base_url='https://example.com/v1' WHERE id=?",(p,))
    response=c.post(f'/api/meetings/{m}/jobs',json={'completion_mode':'quick','minutes_followup':{'profile_id':p}})
    assert response.status_code==409 and '明确确认' in response.text
    assert not s.all('SELECT id FROM jobs')
    jid=enqueue(setup,True);config=json.loads(s.one('SELECT config FROM jobs WHERE id=?',(jid,))['config'])
    assert config['minutes_followup']['cloud_confirmed'] and 'secret-not-in-job' not in encode(config)

@pytest.mark.parametrize('change',["UPDATE llm_configs SET base_url='http://127.0.0.1:9999/v1' WHERE id=?","UPDATE llm_configs SET model='other' WHERE id=?","DELETE FROM llm_configs WHERE id=?"])
def test_changed_or_deleted_provider_does_not_send_transcript(setup,change):
    c,s,_,p,calls,_,_=setup;job=transcribe(setup,enqueue(setup));s.execute(change,(p,))
    with pytest.raises(ValueError,match='改变|删除'):generate(setup,job)
    assert calls==[]

def test_cancelled_minutes_can_be_retried_without_retranscription(setup):
    c,s,m,_,calls,_,_=setup;job=transcribe(setup,enqueue(setup));c.post(f'/api/jobs/{job["id"]}/cancel').raise_for_status()
    with pytest.raises(Cancelled):c.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(job['id'],)))
    assert calls==[]
    c.post(f'/api/jobs/{job["id"]}/retry').raise_for_status();generate(setup,job)
    assert s.one('SELECT revision FROM meetings WHERE id=?',(m,))['revision']==1
    assert len(s.all("SELECT id FROM jobs WHERE kind='transcription'"))==1


def test_cancel_during_generation_never_publishes_and_validated_checkpoint_can_resume(setup):
    c,s,m,_,calls,hooks,_=setup;job=transcribe(setup,enqueue(setup))
    hooks['generate']=lambda:s.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job['id'],))
    with pytest.raises(Cancelled):generate(setup,job)
    assert not s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'")
    hooks.clear();c.post(f'/api/jobs/{job["id"]}/retry').raise_for_status();generate(setup,job)
    assert len(calls)==1 and len(s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'"))==1


def test_revision_change_before_or_during_model_request_prevents_publication(setup):
    c,s,m,_,calls,hooks,_=setup;job=transcribe(setup,enqueue(setup))
    hooks['generate']=lambda:s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'已改为下周交付'}],'manual')
    with pytest.raises(ValueError,match='资料变化'):generate(setup,job)
    assert not s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'")
    hooks.clear()
    with pytest.raises(ValueError,match='文字稿已变化'):generate(setup,job)
    assert len(calls)==1


def test_asr_conflict_keeps_candidate_without_automatic_minutes(setup):
    c,s,m,_,calls,hooks,_=setup;jid=enqueue(setup)
    hooks['asr']=lambda:s.save_revision(m,[{'id':'manual','start':0,'end':2,'speaker':'甲','text':'人工稿'}],'manual')
    assert transcribe(setup,jid) is None and calls==[]
    assert s.one("SELECT id FROM artifacts WHERE kind='transcript_candidate'")
    assert '自动纪要未启动' in s.one('SELECT stage FROM jobs WHERE id=?',(jid,))['stage']


def test_child_queue_is_idempotent_and_rolls_back_with_parent_publication(setup):
    _,s,m,_,_,_,_=setup
    with pytest.raises(RuntimeError):
        with s.connect() as db:
            enqueue_after_transcription(db,m,'parent',1,{'test':1});raise RuntimeError('simulate crash before commit')
    assert not s.all("SELECT id FROM jobs WHERE kind='minutes'")
    with s.connect() as db:
        first=enqueue_after_transcription(db,m,'parent',1,{'test':1});second=enqueue_after_transcription(db,m,'parent',1,{'test':1})
    assert first==second and len(s.all("SELECT id FROM jobs WHERE kind='minutes'"))==1


def test_commit_before_status_file_write_recovers_success_after_restart(setup,monkeypatch):
    from app.minutes_generation import MinutesGeneration
    c,s,m,_,_,_,_=setup;job=transcribe(setup,enqueue(setup));original=MinutesGeneration._write
    with monkeypatch.context() as patch:
        def fail(self,folder,value):
            if value['state']=='completed':raise OSError('synthetic interruption after database commit')
            original(self,folder,value)
        patch.setattr(MinutesGeneration,'_write',fail)
        with pytest.raises(OSError):generate(setup,job)
    status=MinutesGeneration(s).status(m)
    assert status['state']=='completed' and s.one('SELECT state FROM jobs WHERE id=?',(job['id'],))['state']=='completed'
    assert len(s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'"))==1



def test_stale_cloud_confirmation_never_uses_new_destination(setup):
    c,s,m,p,calls,_,_=setup
    s.execute("UPDATE llm_configs SET provider_type='cloud',base_url='https://new.example.com/v1' WHERE id=?",(p,))
    response=c.post(f'/api/meetings/{m}/jobs',json={'completion_mode':'quick','minutes_followup':{'profile_id':p,'confirm_cloud':True,'expected_provider':{'id':p,'provider_type':'cloud','base_url':'https://old.example.com/v1','model':'synthetic-model'}}})
    assert response.status_code==409 and not calls and not s.all('SELECT id FROM jobs')



def test_asr_failure_never_starts_minutes_and_preserves_original_audio(setup):
    c,s,m,_,calls,hooks,audio=setup;jid=enqueue(setup)
    def fail():raise RuntimeError('synthetic ASR failure')
    hooks['asr']=fail
    with pytest.raises(RuntimeError,match='ASR failure'):transcribe(setup,jid)
    assert not s.one("SELECT id FROM jobs WHERE kind='minutes'") and calls==[]
    assert (s.directory(m)/'original').read_bytes()==audio


def test_empty_model_output_never_becomes_completed_minutes(setup,monkeypatch):
    c,s,m,_,_,_,_=setup;job=transcribe(setup,enqueue(setup))
    monkeypatch.setattr('app.auto_minutes.OpenAICompatibleProvider.generate_ai_minutes',lambda *args:AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='空输出')))
    with pytest.raises(ValueError,match='有效纪要内容'):generate(setup,job)
    assert not s.one("SELECT id FROM artifacts WHERE kind='meeting_minutes'")
