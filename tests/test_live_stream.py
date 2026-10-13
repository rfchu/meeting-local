import asyncio,io,json,os,sys,threading,wave
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.live_results import LiveResults
from app.recordings import RecordingStore
from app.live_stream import qwen_events

class StreamingProvider:
    def __init__(self):self.mode='success';self.delay=.02;self.calls=0
    def identity(self):return []
    def ready(self):return True
    def stream_command(self,audio,output):
        self.calls+=1
        script="""
import json,time,sys,os
from pathlib import Path
output=Path(sys.argv[1]);(output.parent.parent/'child.pid').write_text(str(os.getpid()))
print(json.dumps({'type':'qwen_partial','text':'会议'},ensure_ascii=False),flush=True)
time.sleep(float(sys.argv[2]))
print(json.dumps({'type':'qwen_partial','text':'会议确认交付。'},ensure_ascii=False),flush=True)
if sys.argv[3]=='failure':sys.exit(1)
text='不一致' if sys.argv[3]=='mismatch' else '会议确认交付。'
output.write_text(json.dumps({'segments':[{'start':0,'end':15,'text':text}]}))
"""
        return [sys.executable,'-c',script,str(output),str(self.delay),self.mode]

def wav(silent=False):
    output=io.BytesIO()
    with wave.open(output,'wb') as source:
        source.setnchannels(1);source.setsampwidth(2);source.setframerate(16000)
        source.writeframes((b'\0\0' if silent else b'\1\0')*240000)
    return output.getvalue()

@pytest.fixture
def setup(tmp_path,monkeypatch):
    provider=StreamingProvider()
    monkeypatch.setattr('app.main.get_asr_provider',lambda *_:provider)
    application=create_app(tmp_path,start_worker=False)
    with TestClient(application) as client:
        rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        client.put(f'/api/recordings/{rid}/metadata',json={'duration':15,'chunk_count':1,'notes':[]})
        yield client,rid,provider

def events(client,rid,silent=False):
    response=client.post('/api/live-transcribe/stream',data={'recording_id':rid,'offset':0},files={'file':('chunk.wav',wav(silent),'audio/wav')})
    assert response.status_code==200,response.text
    assert 'application/x-ndjson' in response.headers['content-type']
    return [json.loads(line) for line in response.text.splitlines()]

def test_real_child_streams_then_only_final_enters_coverage(setup):
    client,rid,provider=setup
    result=events(client,rid)
    assert [e['type'] for e in result]==['start','partial','partial','final']
    assert [e['seq'] for e in result]==[0,1,2,3]
    assert len({e['request_id'] for e in result})==1
    assert result[-1]['result']['segments'][0]['text']==result[-2]['text']
    coverage=client.get(f'/api/recordings/{rid}/live-results?engine=qwen').json()
    assert coverage['covered_seconds']==15
    cached=events(client,rid)
    assert [e['type'] for e in cached]==['start','final']
    assert cached[-1]['result']['cached'] and provider.calls==1

@pytest.mark.parametrize('mode',['failure','mismatch'])
def test_partial_errors_never_claim_complete_coverage_and_retry_works(setup,mode):
    client,rid,provider=setup;provider.mode=mode
    result=events(client,rid)
    assert result[-1]['type']=='error' and any(e['type']=='partial' for e in result)
    row=client.app.state.store.one('SELECT state,segments FROM live_asr_ranges WHERE recording_id=?',(rid,))
    assert row['state']=='failed' and json.loads(row['segments'])==[]
    assert client.get(f'/api/recordings/{rid}/live-results?engine=qwen').json()['covered_seconds']==0
    provider.mode='success'
    assert events(client,rid)[-1]['type']=='final'
    assert provider.calls==2

def test_closing_stream_after_first_partial_kills_child_releases_lock_and_fails_range(setup):
    client,rid,provider=setup;provider.delay=20
    store=client.app.state.store;lock=threading.Lock();cache=LiveResults(store,RecordingStore(store))
    async def run():
        generator=qwen_events(provider,cache,lock,wav(),0,rid,'browser',store.root)
        assert json.loads(await anext(generator))['type']=='start'
        partial=json.loads(await asyncio.wait_for(anext(generator),2))
        assert partial['type']=='partial'
        assert cache.overview(rid,'qwen')['covered_seconds']==0
        assert lock.locked()
        pid=int((store.root/'child.pid').read_text())
        await generator.aclose()
        assert not lock.locked()
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
    asyncio.run(run())
    assert cache.overview(rid,'qwen')['attempts']=={'failed':1}
    assert list(store.root.glob('meeting-stream-*'))==[]

def test_silent_stream_skips_inference_but_commits_coverage(setup):
    client,rid,provider=setup
    result=events(client,rid,silent=True)
    assert result[-1]['result']['segments']==[] and provider.calls==0
    assert client.get(f'/api/recordings/{rid}/live-results?engine=qwen').json()['covered_seconds']==15

def test_native_stream_is_range_bound_and_validates_engine(setup,monkeypatch):
    client,_,provider=setup
    recordings=RecordingStore(client.app.state.store);session=recordings.create('audio/wav');session['source']='macos'
    rid=session['id'];RecordingStore.write(recordings.folder(rid)/'session.json',session)
    monkeypatch.setattr('app.main.NativeCapture.draft_window',lambda *args:{'ready':True,'payload':wav(),'silent':False,'offset':30,'next_offset':45,'skipped_seconds':15})
    response=client.post(f'/api/native-capture/{rid}/draft/stream',json={'engine':'qwen','offset':15})
    result=[json.loads(line) for line in response.text.splitlines()]
    assert result[-1]['result']['next_offset']==45 and result[-1]['result']['segments'][0]['start']==30
    assert all(e['recording_id']==rid for e in result)
    assert client.post(f'/api/native-capture/{rid}/draft/stream',json={'engine':'whisper'}).status_code==400
    assert client.post('/api/live-transcribe/stream',files={'file':('bad.wav',b'bad')}).status_code==400


def test_cumulative_token_decode_holds_incomplete_unicode(monkeypatch,capsys):
    import importlib.util,types
    from pathlib import Path
    stub=types.ModuleType('mlx_audio.lm.sample_utils');stub.make_sampler=lambda *args:None
    monkeypatch.setitem(sys.modules,'mlx_audio.lm.sample_utils',stub)
    source=Path(__file__).resolve().parents[1]/'scripts/qwen_transcribe.py'
    spec=importlib.util.spec_from_file_location('qwen_stream_script',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Tokenizer:
        def decode(self,tokens,**kwargs):return ['会\ufffd','会议','会议确认。'][len(tokens)-1]
    class Model:
        _tokenizer=Tokenizer()
        def stream_generate(self,*args,**kwargs):return iter([(1,None),(2,None),(3,None)])
    text,attempts=module.transcribe_streamed(Model(),[0]*16000,16000,max_tokens=10)
    events=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e['text'] for e in events]==['会议','会议确认。']
    assert text==events[-1]['text'] and attempts[0]['generation_tokens']==3
    with pytest.raises(RuntimeError,match='输出上限'):module.transcribe_streamed(Model(),[0]*16000,16000,max_tokens=3)


def test_cancel_during_read_cleans_child_and_marks_disconnect(setup):
    client,rid,provider=setup;provider.delay=20
    store=client.app.state.store;cache=LiveResults(store,RecordingStore(store));lock=threading.Lock()
    async def run():
        generator=qwen_events(provider,cache,lock,wav(),0,rid,'browser',store.root)
        await anext(generator);assert json.loads(await anext(generator))['type']=='partial'
        pid=int((store.root/'child.pid').read_text())
        task=asyncio.create_task(anext(generator));await asyncio.sleep(0);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not lock.locked()
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
    asyncio.run(run())
    row=store.one('SELECT state,error FROM live_asr_ranges WHERE recording_id=?',(rid,))
    assert row['state']=='failed' and '连接中断' in row['error']

def test_stream_timeout_returns_reason_and_leaves_range_uncovered(setup,monkeypatch):
    client,rid,provider=setup;provider.delay=20
    timeout=asyncio.timeout
    monkeypatch.setattr('app.live_stream.asyncio.timeout',lambda _:timeout(.1))
    result=events(client,rid)
    assert result[-1]['type']=='error' and '超时' in result[-1]['message']
    assert client.get(f'/api/recordings/{rid}/live-results?engine=qwen').json()['covered_seconds']==0
