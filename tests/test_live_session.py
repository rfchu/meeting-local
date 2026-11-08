import asyncio,io,json,threading,wave
import pytest
from fastapi.testclient import TestClient
from app.db import Store
from app.recordings import RecordingStore
from app.native_capture import NativeCapture
from app.live_results import LiveResults
from app.live_session import LiveSessions,stable_prefix,preview_step,pcm_wav
from app.main import create_app

class Provider:
    def ready(self):return True
    def identity(self):return []
    def resident_command(self):return []
class Resident:
    def __init__(self):self.calls=[];self.block=None;self.fail=False
    async def generate(self,provider,audio,context=''):
        with wave.open(str(audio),'rb') as wav:duration=wav.getnframes()/16000
        self.calls.append(duration)
        text='我们讨论听记产品的下一步功能'+('安排。' if duration>3 else '')
        yield {'type':'qwen_partial','text':text}
        if self.block:await self.block.wait()
        if self.fail:raise RuntimeError('合成识别失败')
        yield {'type':'qwen_final','segments':[{'start':0,'end':duration,'text':text}]}

def setup(tmp_path):
    store=Store(tmp_path);recordings=RecordingStore(store);native=NativeCapture(store,recordings)
    resident=Resident();cache=LiveResults(store,recordings);lock=threading.Lock()
    manager=LiveSessions(store,recordings,native,cache,lock,resident,Provider)
    rid=recordings.create('audio/webm')['id']
    return manager,recordings,rid,resident,lock

async def until(check):
    async with asyncio.timeout(4):
        while not check():await asyncio.sleep(.02)

def audio(seconds):return pcm_wav(b'\x70\x17'*round(seconds*16000))
CONFIG={'responsive':True,'context_enabled':True,'terms':'听记'}

def test_stable_prefix_is_a_display_hint_with_tail_reserve_and_unicode_boundaries():
    assert stable_prefix('我们讨论听记产品的下一步功能','我们讨论听记产品的下一步功能安排。')=='我们讨论听记产品的下一'
    assert stable_prefix('旧的结果','新的结果')==''
    assert stable_prefix('短句','短句')==''
    assert not stable_prefix('A\u0301abcdefghi','A\u0301abcdefghi',reserve=10).endswith('A')
    assert [preview_step(ms) for ms in (100,2000,10000)]==[2,3,5]

def test_backend_runs_without_subscribers_and_reconnect_snapshot_does_not_restart_asr(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            manager.ingest(rid,0,audio(3))
            await until(lambda:bool(json.loads(manager.row(rid)['partial'])))
            snapshot=manager.snapshot(rid)
            assert snapshot['partial']['text'] and not snapshot['segments']
            assert manager.cache.overview(rid,'qwen',3)['covered_seconds']==0
            subscriber=manager.events(rid,0)
            assert json.loads(await anext(subscriber))['type']=='snapshot'
            await subscriber.aclose() # closing UI transport must not cancel model work
            assert not manager.tasks[rid].done()
            await manager.start(rid,CONFIG)
            assert len(resident.calls)==1
            manager.ingest(rid,3,audio(2))
            await until(lambda:bool(json.loads(manager.row(rid)['partial']).get('stable_text')))
            manager.ingest(rid,5,audio(10))
            await until(lambda:json.loads(manager.row(rid)['metrics']).get('extending'))
            assert manager.row(rid)['cursor']==0
            manager.checkpoint(rid,status='paused')
            await until(lambda:manager.row(rid)['cursor']==15*16000)
            assert manager.cache.overview(rid,'qwen',15)['covered_seconds']==15
            reconnect=manager.snapshot(rid)
            assert len(reconnect['segments'])==1 and reconnect['segments'][0]['live_range_id']
            assert not reconnect['partial'] and resident.calls==[3,5,15,15]
            replay=manager.events(rid,snapshot['seq'])
            assert json.loads(await anext(replay))['seq']==snapshot['seq']+1
            await replay.aclose()
            assert not lock.locked()
        finally:await manager.close()
    asyncio.run(run())

def test_ingress_is_idempotent_rejects_overlap_and_gap_never_claims_coverage(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            assert not manager.ingest(rid,0,audio(3))['cached']
            assert manager.ingest(rid,0,audio(3))['cached']
            with pytest.raises(ValueError,match='冲突'):manager.ingest(rid,2,audio(3))
            manager.ingest(rid,5,audio(3))
            manager.checkpoint(rid,status='paused')
            await until(lambda:manager.row(rid)['cursor']==8*16000)
            overview=manager.cache.overview(rid,'qwen',8)
            assert overview['gaps']==[{'start':3,'end':5}]
            assert overview['covered_seconds']==6
        finally:await manager.close()
    asyncio.run(run())

def test_service_restart_replays_only_unconfirmed_audio_and_finish_flushes_short_tail(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        await manager.start(rid,CONFIG);manager.ingest(rid,0,audio(3))
        await until(lambda:bool(json.loads(manager.row(rid)['partial'])))
        await manager.close()
        restarted=LiveSessions(manager.store,recordings,manager.native,manager.cache,lock,resident,Provider)
        try:
            await restarted.resume()
            restarted.ingest(rid,3,audio(1))
            await restarted.finish(rid)
            assert restarted.row(rid)['status']=='stopped'
            assert restarted.cache.overview(rid,'qwen',4)['covered_seconds']==4
            before=len(resident.calls)
            await restarted.finish(rid)
            assert len(resident.calls)==before
        finally:await restarted.close()
    asyncio.run(run())

def test_finish_timeout_cancels_optional_inference_and_preserves_input(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path);resident.block=asyncio.Event()
        try:
            await manager.start(rid,CONFIG);manager.ingest(rid,0,audio(3))
            await until(lambda:bool(resident.calls))
            assert lock.locked()
            await manager.finish(rid,timeout=.01)
            assert not lock.locked() and manager.row(rid)['status']=='stopped'
            assert manager.cache.overview(rid,'qwen',3)['covered_seconds']==0
            assert list((recordings.folder(rid)/'live-input').glob('*.wav'))
        finally:await manager.close()
    asyncio.run(run())

def test_old_cursor_receives_snapshot_when_bounded_journal_has_expired(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            for i in range(230):manager.emit(rid,'status',message=f'事件{i}')
            assert manager.store.one('SELECT COUNT(*) AS n FROM live_session_events')['n']==192
            events=manager.events(rid,1)
            event=json.loads(await anext(events))
            assert event['type']=='snapshot' and event['seq']==230
            await events.aclose()
        finally:await manager.close()
    asyncio.run(run())

def test_api_lifecycle_validation_and_closed_stream_replay(tmp_path,monkeypatch):
    monkeypatch.setattr('app.main.get_asr_provider',lambda *_:Provider())
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        url=f'/api/recordings/{rid}/live-session'
        assert client.put(url,json=CONFIG).status_code==200
        assert client.put(url,json=CONFIG).status_code==200
        assert client.put(url,json=CONFIG|{'terms':'不同术语'}).status_code==409
        assert client.post(url+'/audio',data={'offset':'nan'},files={'file':('chunk.wav',audio(3),'audio/wav')}).status_code==422
        assert client.post(url+'/audio',data={'offset':'0'},files={'file':('chunk.wav',b'bad','audio/wav')}).status_code==409
        assert client.post(url+'/finish').status_code==200
        response=client.get(url+'/events?after=0')
        assert response.status_code==200
        events=[json.loads(line) for line in response.text.splitlines()]
        assert events[0]['type']=='snapshot' and events[-1]['type']=='ended'
        assert client.put(url,json=CONFIG).status_code==409

def test_native_supervisor_reads_saved_tracks_without_frontend_polling_and_flushes_stop(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        session=recordings.read(rid);session.update(source='macos',include_microphone=False)
        folder=recordings.folder(rid);recordings.write(folder/'session.json',session)
        class Process:
            def poll(self):return None
        manager.native.processes[rid]=Process()
        def capture(seconds,state='recording'):
            (folder/'system.pcm').write_bytes(b'\x70\x17'*16000*seconds)
            recordings.write(folder/'capture.json',{'state':state,'duration':seconds,'system_frames':16000*seconds,'microphone_frames':0,'silence_seconds':{'system':0},'error':''})
        try:
            capture(3);await manager.start(rid,CONFIG)
            await until(lambda:bool(json.loads(manager.row(rid)['partial'])))
            assert rid not in manager.native.last_seen # backend ASR is not a client keep-alive
            capture(5)
            await until(lambda:bool(json.loads(manager.row(rid)['partial']).get('stable_text')))
            subscriber=manager.events(rid,0)
            await anext(subscriber);await subscriber.aclose()
            capture(7,'stopped')
            await until(lambda:manager.row(rid)['status']=='stopped')
            assert manager.cache.overview(rid,'qwen',7)['covered_seconds']==7
            assert resident.calls==[3,5,7]
        finally:await manager.close()
    asyncio.run(run())

def test_concurrent_finish_requests_are_idempotent_even_if_one_deadline_expires(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path);resident.block=asyncio.Event()
        try:
            await manager.start(rid,CONFIG);manager.ingest(rid,0,audio(3))
            await until(lambda:bool(resident.calls))
            await asyncio.gather(manager.finish(rid,timeout=.01),manager.finish(rid,timeout=1))
            assert manager.row(rid)['status']=='stopped' and not lock.locked()
        finally:await manager.close()
    asyncio.run(run())

def test_duplicate_pcm_cache_is_removed_only_after_original_recording_is_published(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG);manager.ingest(rid,0,audio(3))
            manager.published(rid)
            assert manager.input_end(rid)==3*16000
            await manager.finish(rid)
            directory=manager.store.directory(rid);directory.mkdir();(directory/'original').write_bytes(b'original recording')
            manager.store.execute("INSERT INTO meetings(id,title,filename,duration) VALUES(?,'synthetic','test.webm',3)",(rid,))
            manager.published(rid)
            assert not (recordings.folder(rid)/'live-input').exists()
            assert (directory/'original').read_bytes()==b'original recording'
            assert manager.cache.overview(rid,'qwen',3)['covered_seconds']==3
        finally:await manager.close()
    asyncio.run(run())


def test_dual_capture_keeps_overlapping_evidence_and_reads_the_requested_track(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    services=app.state.services
    recordings=services.recordings
    session=recordings.create('audio/wav')
    rid=session['id'];folder=recordings.folder(rid)
    recordings.write(folder/'session.json',session|{'source':'macos','include_microphone':True})
    tracks={'system':b'\x71\x15'*16000,'microphone':b'\x64\x17'*16000}
    for track,pcm in tracks.items():(folder/(track+'.pcm')).write_bytes(pcm)
    aid,_=services.live_results.begin(rid,'native','qwen',0,1,b'fixture',{'track_mode':'dual-v1'})
    services.live_results.complete(aid,[
        {'start':0,'end':1,'source_track':track,'text':text}
        for track,text in [('system','周五交付'),('microphone','周四检查')]])
    parts,_=services.live_overview.source(rid,'qwen')
    assert [(part['source_track'],part['start']) for part in parts]==[('system',0),('microphone',0)]
    client=TestClient(app)
    for i,track in enumerate(tracks):
        response=client.get(f'/api/recordings/{rid}/live-audio/{aid}/{i}')
        assert response.status_code==200
        with wave.open(io.BytesIO(response.content),'rb') as wav:
            assert wav.readframes(16000)==tracks[track]
    assert client.get(f'/api/recordings/{"0"*32}/live-audio/{aid}/0').status_code==404
    assert client.get(f'/api/recordings/{rid}/live-audio/{aid}/-1').status_code==404


def test_dual_language_rejection_preserves_the_other_track_and_reports_gap(tmp_path):
    from app.languages import AUTO_LANGUAGE_ERROR

    class MixedResident(Resident):
        async def generate(self,provider,path,context=''):
            with wave.open(str(path),'rb') as wav:
                if wav.readframes(1)==b'\x00\x08':
                    raise RuntimeError(AUTO_LANGUAGE_ERROR)
            async for event in super().generate(provider,path,context):
                yield event

    manager,recordings,rid,_,_=setup(tmp_path)
    manager.resident=MixedResident()
    class AutoProvider(Provider):
        def identity(self):return [{"source_language":self.language}]
    manager.provider=AutoProvider
    folder=recordings.folder(rid)
    recordings.write(folder/'session.json',recordings.read(rid)|{'source':'macos','include_microphone':True})
    result,error=asyncio.run(manager.recognize_tracks(rid,{'engine':'qwen','source_language':'auto'},{
        'offset':0,'next_offset':3,'track_payloads':{'system':audio(3),'microphone':pcm_wav(b'\x00\x08\x00\xf8'*24000)}}))
    assert not error
    assert [part['source_track'] for part in result['segments']]==['system']
    assert result['failed_tracks']==['microphone']
    [row]=manager.cache.snapshot(rid,'qwen','auto')
    assert row['failed_tracks']==['microphone']
    assert manager.cache.overview(rid,'qwen',3)['covered_seconds']==0


def test_dual_preview_is_ephemeral_and_deduplicated_only_at_confirmation(tmp_path,monkeypatch):
    manager,recordings,rid,_,_=setup(tmp_path)
    folder=recordings.folder(rid)
    recordings.write(folder/'session.json',recordings.read(rid)|{'source':'macos','include_microphone':True})
    monkeypatch.setattr('app.track_audio.wav_echo_evidence',lambda *_:'dominant')

    async def run():
        drafts=[]
        for confirmed in (False,True):
            result,error=await manager.recognize_tracks(rid,{'engine':'qwen','source_language':'zh'},{
                'offset':0,'next_offset':3,'confirmed':confirmed,
                'track_payloads':{'system':audio(3),'microphone':pcm_wav(b'\x00\x08\x00\xf8'*24000)},
            },drafts.append)
            assert not error
            assert {part['source_track'] for part in drafts}=={'system','microphone'}
            assert result['first_text_ms'] is not None
            rows=manager.cache.snapshot(rid,'qwen')
            if confirmed:
                assert [part['source_track'] for part in result['segments']]==['system']
                assert len(rows)==1 and len(rows[0]['segments'])==1
            else:
                assert len(result['segments'])==2 and not rows
                assert not any('live_range_id' in part for part in result['segments'])
            drafts.clear()
    asyncio.run(run())


def test_dual_supervisor_revises_each_track_without_sharing_prefix(tmp_path,monkeypatch):
    async def run():
        manager,recordings,rid,_,_=setup(tmp_path)
        folder=recordings.folder(rid)
        recordings.write(folder/'session.json',recordings.read(rid)|{'source':'macos','include_microphone':True})
        track_text={'system':'线上同事正在讨论项目下周发布的时间安排',
                    'microphone':'现场同事正在核对明天提交的材料和清单'}
        calls=[]

        class TrackResident:
            supports_continuation=True

            async def generate(self,provider,path,context='',continuation=None):
                track=continuation['key'].rsplit(':',1)[-1]
                with wave.open(str(path),'rb') as wav:duration=wav.getnframes()/16000
                calls.append((track,continuation))
                text=track_text[track]+('还要确认日期' if duration<=3 else '日期已经确认')
                yield {'type':'qwen_partial','text':text}
                yield {'type':'qwen_final','segments':[{'start':0,'end':duration,'text':text}]}

        manager.resident=TrackResident()
        capture={'state':'recording','duration':3}
        monkeypatch.setattr(manager.native,'status',lambda *args,**kwargs:dict(capture,system_frames=capture['duration']*16000,microphone_frames=capture['duration']*16000))
        monkeypatch.setattr('app.track_audio.reduce_echo_wav',lambda _,microphone:microphone)
        for name in track_text:
            (folder/f'{name}.pcm').write_bytes(b'\x70\x17'*16000*7)
        try:
            await manager.start(rid,CONFIG)
            await until(lambda:len(json.loads(manager.row(rid)['partial']).get('tracks',[]))==2)
            capture['duration']=5
            await until(lambda:all(
                part.get('stable_text') and part['text'].endswith('日期已经确认')
                for part in json.loads(manager.row(rid)['partial']).get('tracks',[])
            ) and len(calls)>=4)
            for track,continuation in calls[2:4]:
                assert continuation['previous_text']==track_text[track]+'还要确认日期'
            preview=manager.snapshot(rid)['partial']['tracks']
            assert {part['source_track']:part['stable_text'] for part in preview}==track_text
            assert not manager.cache.snapshot(rid,'qwen')
        finally:await manager.close()
    asyncio.run(run())
