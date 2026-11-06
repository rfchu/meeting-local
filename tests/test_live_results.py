import io,json,wave
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.recordings import RecordingStore
from app.live_results import plan_ranges

def audio(seconds=15,silent=False):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        wav.writeframes((b'\0\0' if silent else b'\1\0')*round(seconds*16000))
    return stream.getvalue()

class Provider:
    engine_id='whisper';label='Whisper test';model='local-test';chunk_seconds=300;needs_punctuation=False
    def __init__(self):self.calls=[];self.error=False
    def identity(self):return []
    def ready(self):return True
    def transcribe_live(self,path,output,run):
        self.calls.append('draft')
        if self.error:raise RuntimeError('测试识别失败')
        with wave.open(str(path),'rb') as wav:duration=wav.getnframes()/16000
        return [{'start':0,'end':duration,'text':'实时文本' if duration==15 else '补齐尾段'}]
    def transcribe(self,*args):
        self.calls.append('full');return [{'start':0,'end':1,'text':'精细文本'}]

@pytest.fixture
def setup(tmp_path,monkeypatch):
    provider=Provider()
    monkeypatch.setattr('app.main.get_asr_provider',lambda *_:provider)
    monkeypatch.setattr('app.worker.get_asr_provider',lambda *_:provider)
    application=create_app(tmp_path,start_worker=False)
    with TestClient(application) as client:
        rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        client.put(f'/api/recordings/{rid}/metadata',json={'duration':18,'chunk_count':1,'notes':[]})
        yield client,rid,provider

def live(client,rid,offset=0,seconds=15,silent=False):
    return client.post('/api/live-transcribe',data={'recording_id':rid,'offset':offset},
                       files={'file':('chunk.wav',audio(seconds,silent),'audio/wav')})
def finish(client,rid):
    client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('chunk',audio(18))}).raise_for_status()
    client.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1}).raise_for_status()

def process(client,rid,mode='quick'):
    response=client.post(f'/api/meetings/{rid}/jobs',json={'completion_mode':mode})
    assert response.status_code==201,response.text
    jid=response.json()['id'];store=client.app.state.store
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    return jid

def test_persistence_retry_silence_and_coverage_survive_restart(setup):
    client,rid,provider=setup
    assert live(client,rid).json()['persisted']
    assert live(client,rid).json()['cached'] and provider.calls==['draft']
    provider.error=True
    assert live(client,rid,15,3).status_code==503
    coverage=client.get(f'/api/recordings/{rid}/live-results').json()
    assert coverage['covered_seconds']==15 and coverage['gaps']==[{'start':15,'end':18}]
    assert coverage['attempts']=={'completed':1,'failed':1}
    assert coverage['recent_failures'][0]['error']=='测试识别失败'
    root=client.app.state.store.root
    client.__exit__(None,None,None)  # Release the previous service before a real restart.
    with TestClient(create_app(root,start_worker=False)) as restarted:
        assert restarted.get(f'/api/recordings/{rid}/live-results').json()['covered_seconds']==15
        result=live(restarted,rid,15,3,silent=True)
        assert result.status_code==200 and result.json()['segments']==[]
        assert restarted.get(f'/api/recordings/{rid}/live-results').json()['covered_seconds']==18

def test_fast_completion_reuses_cached_text_and_only_decodes_tail(setup):
    client,rid,provider=setup
    live(client,rid).raise_for_status();finish(client,rid)
    provider.calls=[];jid=process(client,rid)
    assert provider.calls==['draft']
    meeting=client.get(f'/api/meetings/{rid}').json()
    assert [s['text'] for s in meeting['segments']]==['实时文本','补齐尾段']
    assert meeting['segments'][1]['start']==15 and meeting['segments'][1]['end']==18
    revision=client.get(f'/api/meetings/{rid}/revisions/1').json()
    provenance=json.loads(revision['source'])
    assert provenance['kind']=='live_reuse' and provenance['reused_seconds']==15
    assert provenance['patched_ranges']==[{'start':15,'end':18}]
    assert not provenance['diarization'] and provenance['timestamp_precision']=='draft'
    assert client.app.state.store.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']=='completed'

def test_fully_covered_fast_completion_never_calls_asr(setup):
    client,rid,provider=setup
    live(client,rid).raise_for_status();live(client,rid,15,3).raise_for_status();finish(client,rid)
    provider.calls=[];provider.error=True
    process(client,rid)
    assert provider.calls==[]
    assert len(client.get(f'/api/meetings/{rid}').json()['segments'])==2

def test_full_processing_still_retranscribes_and_retains_fast_revision(setup):
    client,rid,provider=setup
    live(client,rid).raise_for_status();finish(client,rid)
    process(client,rid);provider.calls=[]
    process(client,rid,'full')
    assert provider.calls==['full']
    assert client.get(f'/api/meetings/{rid}/revisions/1').json()['segments'][0]['text']=='实时文本'
    assert client.get(f'/api/meetings/{rid}').json()['segments'][0]['text']=='精细文本'

def test_fast_job_uses_immutable_snapshot_and_preserves_user_edits(setup):
    client,rid,provider=setup
    live(client,rid).raise_for_status();finish(client,rid)
    jid=client.post(f'/api/meetings/{rid}/jobs',json={'completion_mode':'quick'}).json()['id']
    live(client,rid,15,3).raise_for_status() # Late live result does not alter queued plan.
    edited=[{'id':'manual','start':0,'end':1,'text':'用户校对','speaker':'甲'}]
    client.put(f'/api/meetings/{rid}/transcript',json={'revision':0,'segments':edited}).raise_for_status()
    provider.calls=[];store=client.app.state.store
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert provider.calls==['draft']
    assert client.get(f'/api/meetings/{rid}').json()['segments'][0]['text']=='用户校对'
    assert store.one("SELECT kind FROM artifacts WHERE meeting_id=?",(rid,))['kind']=='transcript_candidate'

def test_delete_cleans_cache_and_rejects_late_writes(setup):
    client,rid,provider=setup
    live(client,rid).raise_for_status();finish(client,rid)
    assert client.delete(f'/api/meetings/{rid}').status_code==200
    assert client.app.state.store.all('SELECT * FROM live_asr_ranges')==[]
    assert live(client,rid).status_code==409

def test_gap_plan_never_treats_failed_or_overlap_as_coverage():
    rows=[{'id':'a','start_frame':0,'end_frame':15*16000},
          {'id':'b','start_frame':10*16000,'end_frame':25*16000},
          {'id':'c','start_frame':30*16000,'end_frame':45*16000}]
    reused,gaps=plan_ranges(rows,40)
    assert [r['id'] for r in reused]==['a','c']
    assert gaps==[(15,30)] and reused[-1]['end_frame']==40*16000

def test_imported_meeting_cannot_use_recording_cache(setup):
    client,rid,provider=setup
    mid=client.post('/api/meetings',files={'file':('file.wav',audio(),'audio/wav')}).json()['id']
    assert client.post(f'/api/meetings/{mid}/jobs',json={'completion_mode':'quick'}).status_code==409
    assert client.post('/api/live-transcribe',data={'recording_id':rid,'source':'native'},files={'file':('x.wav',audio(),'audio/wav')}).status_code==409

def test_complete_cache_can_finish_even_if_model_is_unavailable(setup,monkeypatch):
    client,rid,provider=setup
    live(client,rid).raise_for_status();live(client,rid,15,3).raise_for_status();finish(client,rid)
    provider.calls=[];monkeypatch.setattr(provider,'ready',lambda:False)
    process(client,rid)
    assert provider.calls==[] and client.get(f'/api/meetings/{rid}').json()['revision']==1

def test_pending_attempt_is_failed_after_service_restart(setup):
    client,rid,provider=setup;store=client.app.state.store
    from app.live_results import LiveResults
    aid,_=LiveResults(store,RecordingStore(store)).begin(rid,'browser','whisper',0,15,audio(),[])
    client.__exit__(None,None,None)  # Release the previous service before a real restart.
    with TestClient(create_app(store.root,start_worker=False)) as restarted:
        overview=restarted.get(f'/api/recordings/{rid}/live-results').json()
        assert overview['attempts']['failed']==1 and overview['covered_seconds']==0
        assert '服务重启' in overview['recent_failures'][0]['error']
