import asyncio,io,json,os,sys,time,threading,wave
import pytest
from pathlib import Path
from app.qwen_resident import QwenResident
from app.db import Store
from app.worker import Worker,Cancelled
from app.live_results import LiveResults
from app.recordings import RecordingStore
from app.live_stream import qwen_events

class ResidentProvider:
    model='fixture-model'
    def __init__(self,delay=.01):self.delay=delay;self.revision=0
    def identity(self):return [{'version':self.revision}]
    def ready(self):return True
    def resident_command(self):
        code="""
import sys,json,time,wave
print(json.dumps({'type':'ready','memory_gb':1,'peak_memory_gb':2,'load_ms':10}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    with wave.open(request['audio'],'rb') as wav:duration=wav.getnframes()/16000
    text='临时短句' if duration<=5 else '修订后的完整句'
    print(json.dumps({'type':'qwen_partial','id':request['id'],'text':text}),flush=True)
    time.sleep(float(sys.argv[1]))
    print(json.dumps({'type':'qwen_final','id':request['id'],'segments':[{'start':0,'end':duration,'text':text}],'peak_memory_gb':2.5}),flush=True)
"""
        return [sys.executable,'-c',code,str(self.delay)]

def audio(path,duration=5):
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\1\0'*round(duration*16000))
    return path

def test_model_is_reused_and_reloads_after_identity_change(tmp_path):
    async def run():
        pool=QwenResident(tmp_path);provider=ResidentProvider();path=audio(tmp_path/'audio.wav')
        try:
            status=await pool.warm(provider);assert status['loaded'] and not status['busy']
            pid=pool.process.pid
            for _ in range(2):
                events=[event async for event in pool.generate(provider,path)]
                assert events[-1]['type']=='qwen_final'
            assert pool.process.pid==pid and pool.status()['load_count']==1 and pool.status()['requests']==2
            provider.revision=1;await pool.warm(provider)
            assert pool.process.pid!=pid and pool.status()['load_count']==2
        finally:await pool.shutdown()
        assert not pool.status()['loaded']
    asyncio.run(run())

def test_cancellation_kills_resident_and_next_request_reloads(tmp_path):
    async def run():
        pool=QwenResident(tmp_path);provider=ResidentProvider(delay=10);path=audio(tmp_path/'audio.wav')
        generator=pool.generate(provider,path)
        assert (await anext(generator))['type']=='qwen_partial';pid=pool.process.pid
        await generator.aclose()
        assert not pool.status()['loaded'] and not pool.busy
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
        provider.delay=.01
        await pool.warm(provider);assert pool.status()['load_count']==2
        await pool.shutdown()
    asyncio.run(run())

def test_idle_expiry_releases_model_but_never_releases_busy_task(tmp_path):
    async def run():
        pool=QwenResident(tmp_path,idle_seconds=1);await pool.warm(ResidentProvider())
        pool.last_used=time.monotonic()-2;pool.busy=True
        await pool.expire_idle();assert pool.status()['loaded']
        pool.busy=False;await pool.expire_idle();assert not pool.status()['loaded']
        await pool.shutdown()
    asyncio.run(run())

def test_previews_do_not_enter_coverage_and_larger_window_confirms_once(tmp_path):
    async def run():
        store=Store(tmp_path);recordings=RecordingStore(store);cache=LiveResults(store,recordings)
        session=recordings.create('audio/webm');rid=session['id'];recordings.metadata(rid,[],15,0)
        pool=QwenResident(tmp_path);provider=ResidentProvider();lock=threading.Lock()
        try:
            for seconds,confirmed in [(5,False),(10,False),(15,True)]:
                payload=audio(tmp_path/f'{seconds}.wav',seconds).read_bytes()
                events=[json.loads(line) async for line in qwen_events(provider,cache,lock,payload,0,rid,'browser',tmp_path,resident=pool,confirmed=confirmed)]
                assert events[-1]['result']['confirmed']==confirmed
                assert cache.overview(rid,'qwen')['covered_seconds']==(15 if confirmed else 0)
            assert len(cache.snapshot(rid,'qwen'))==1
            assert pool.status()['load_count']==1 and pool.status()['requests']==3
            assert store.one("SELECT COUNT(*) AS n FROM live_asr_ranges WHERE state='preview'")['n']==2
        finally:await pool.shutdown()
    asyncio.run(run())

def test_formal_worker_releases_resident_before_model_work_and_keeps_guard(tmp_path):
    store=Store(tmp_path);jid='job';store.execute("INSERT INTO jobs(id,state,config) VALUES(?,'running','{}')",(jid,))
    worker=Worker(store);worker.asr_lock=threading.Lock();order=[]
    worker.release_model=lambda:order.append('release')
    def process(job):
        assert worker.asr_lock.locked();order.append('process')
    worker.process_transcription=process
    worker.process({'id':jid})
    assert order==['release','process'] and not worker.asr_lock.locked()
    worker.asr_lock.acquire();store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(jid,))
    with pytest.raises(Cancelled):worker.process({'id':jid})
    assert worker.asr_lock.locked();worker.asr_lock.release()

def test_http_warm_preview_and_formal_handoff(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    provider=ResidentProvider();monkeypatch.setattr('app.main.get_asr_provider',lambda *_:provider)
    application=create_app(tmp_path,start_worker=False)
    with TestClient(application) as client:
        assert not client.get('/api/live-model').json()['loaded']
        warmed=client.post('/api/live-model/warm');assert warmed.status_code==200
        assert client.get('/api/live-model').json()['load_count']==1
        rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        client.put(f'/api/recordings/{rid}/metadata',json={'notes':[],'duration':5,'chunk_count':1})
        payload=audio(tmp_path/'five.wav').read_bytes()
        response=client.post('/api/live-transcribe/stream',data={'recording_id':rid,'confirmed':'false'},files={'file':('five.wav',payload,'audio/wav')})
        events=[json.loads(line) for line in response.text.splitlines()]
        assert events[-1]['result']['confirmed'] is False
        assert client.get(f'/api/recordings/{rid}/live-results?engine=qwen').json()['covered_seconds']==0
        assert client.get('/api/live-model').json()['load_count']==1
        worker=application.state.worker;worker.busy_jid='formal'
        assert client.post('/api/live-model/warm').status_code==409
        worker.busy_jid=None;store=application.state.store
        store.execute("INSERT INTO jobs(id,state,config) VALUES('formal','running','{}')")
        worker.process_transcription=lambda job:None
        worker.process({'id':'formal'})
        assert not client.get('/api/live-model').json()['loaded']
        store.execute("UPDATE jobs SET state='completed' WHERE id='formal'")

def test_native_stream_passes_responsive_window_and_does_not_commit_preview(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    provider=ResidentProvider();monkeypatch.setattr('app.main.get_asr_provider',lambda *_:provider)
    application=create_app(tmp_path,start_worker=False);seen=[]
    payload=audio(tmp_path/'five.wav').read_bytes()
    def window(*args):
        seen.append(args[3:])
        return {'ready':True,'payload':payload,'silent':False,'confirmed':False,'offset':0,'next_offset':5,'skipped_seconds':0}
    monkeypatch.setattr('app.main.NativeCapture.draft_window',window)
    with TestClient(application) as client:
        recordings=RecordingStore(application.state.store);session=recordings.create('audio/wav');session['source']='macos'
        rid=session['id'];RecordingStore.write(recordings.folder(rid)/'session.json',session)
        response=client.post(f'/api/native-capture/{rid}/draft/stream',json={'engine':'qwen','responsive':True,'window_seconds':5})
        result=[json.loads(line) for line in response.text.splitlines()][-1]['result']
        assert seen==[(5,True)] and result['confirmed'] is False
        assert application.state.store.all("SELECT * FROM live_asr_ranges WHERE state='completed'")==[]
