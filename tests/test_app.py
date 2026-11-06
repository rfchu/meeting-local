import io, wave
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.providers import attach_speakers
from app.worker import Cancelled

def wav_bytes(seconds=1,silent=False):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes((b'\0\0' if silent else b'\1\0')*int(16000*seconds))
    return out.getvalue()

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as c: yield c

def upload(c):
    r=c.post('/api/meetings',files={'file':('测试会议.wav',wav_bytes(),'audio/wav')})
    assert r.status_code==201
    return r.json()['id']

def sample():return [{'id':'seg1','start':0,'end':.5,'text':'确定周五交付。','speaker':'发言人 1','boundary_review':False,'speaker_locked':False}]

def native_session(client):
    from app.recordings import RecordingStore
    recordings=RecordingStore(client.app.state.store)
    session=recordings.create('audio/wav');session['source']='macos'
    RecordingStore.write(recordings.folder(session['id'])/'session.json',session)
    return session['id']

def test_speaker_review_apply_is_versioned_and_preserves_original(client,monkeypatch):
    mid=upload(client);store=client.app.state.store
    segments=sample()+[dict(sample()[0],id='seg2',start=1,end=2,speaker='发言人 2',speaker_locked=True,text='好的，我来汇报。')]
    store.save_revision(mid,segments,'manual',expected=0)
    monkeypatch.setattr('app.main.LMStudioProvider.resolve_model',lambda *_:'test-model')
    monkeypatch.setattr('app.main.generate_review',lambda *_:{'suggestions':[{
        'segment_id':'seg1','original_speaker':'发言人 1','proposed_speaker':'发言人 2',
        'reason':'连续回应','evidence_ids':['seg2'],'strength':'review'}],'chunks':1})
    result=client.post(f'/api/meetings/{mid}/speaker-reviews',json={})
    assert result.status_code==201
    review=result.json();assert review['revision']==1
    assert client.get(f'/api/meetings/{mid}').json()['segments']==segments
    route=f"/api/meetings/{mid}/speaker-reviews/{review['id']}/apply"
    assert client.post(route,json={'revision':1,'segment_ids':['missing']}).status_code==400
    applied=client.post(route,json={'revision':1,'segment_ids':['seg1']})
    assert applied.status_code==200 and applied.json()['revision']==2
    fresh=client.get(f'/api/meetings/{mid}').json()
    assert fresh['segments'][0]['speaker']=='发言人 2' and fresh['segments'][0]['speaker_locked']
    assert fresh['segments'][0]['text']==segments[0]['text']
    assert client.get(f'/api/meetings/{mid}/revisions/1').json()['segments']==segments
    assert client.post(route,json={'revision':2,'segment_ids':['seg1']}).status_code==409
    assert fresh['artifacts'][0]['stale']

def test_speaker_review_blocks_locked_samples_and_unknown_evidence():
    from app.speaker_review import SpeakerReview,validate_review
    segments=sample()+[dict(sample()[0],id='seg2',speaker='发言人 2',speaker_locked=True)]
    suggestion={'segment_id':'seg1','proposed_speaker':'发言人 2','reason':'上下文','evidence_ids':['seg2'],'strength':'supported'}
    assert len(validate_review(SpeakerReview(suggestions=[suggestion]),segments,{'seg1'}))==1
    for changes in ({'evidence_ids':['missing']},{'proposed_speaker':'不存在的人'},{'segment_id':'seg2','proposed_speaker':'发言人 1'}):
        with pytest.raises(ValueError):validate_review(SpeakerReview(suggestions=[suggestion|changes]),segments,{'seg1','seg2'})

def test_meeting_can_be_renamed(client):
    mid=upload(client)
    response=client.patch(f'/api/meetings/{mid}',json={'title':'新的会议名称'})
    assert response.status_code==200
    assert client.get(f'/api/meetings/{mid}').json()['title']=='新的会议名称'

def test_recording_chunks_retry_and_finish_are_idempotent(client):
    created=client.post('/api/recordings',json={'mime_type':'audio/webm'})
    assert created.status_code==201
    rid=created.json()['id'];payload=wav_bytes();parts=[payload[:1000],payload[1000:]]
    for index in [1,0]:
        assert client.put(f'/api/recordings/{rid}/chunks/{index}',files={'file':('chunk',parts[index])}).status_code==200
    assert client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('chunk',parts[0])}).status_code==200
    assert client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('chunk',b'conflict')}).status_code==409
    assert client.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1}).status_code==409
    notes=[{'captured_at':.5,'note_type':'action','raw_text':'确认交付日期'}]
    assert client.put(f'/api/recordings/{rid}/metadata',json={'notes':notes,'duration':1,'chunk_count':2}).status_code==200
    first=client.post(f'/api/recordings/{rid}/finish',json={'chunk_count':2})
    second=client.post(f'/api/recordings/{rid}/finish',json={'chunk_count':2})
    assert first.status_code==second.status_code==200
    assert first.json()['id']==second.json()['id']==rid
    assert len(second.json()['notes'])==1 and second.json()['notes'][0]['raw_text']==notes[0]['raw_text']
    assert len(client.get('/api/meetings').json())==1
    assert (client.app.state.store.directory(rid)/'original').read_bytes()==payload
    assert client.get('/api/recordings').json()==[]

def test_interrupted_recording_survives_restart_and_missing_chunks(tmp_path):
    root=tmp_path
    with TestClient(create_app(root,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        payload=wav_bytes()
        assert client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('chunk',payload[:1000])}).status_code==200
        assert client.put(f'/api/recordings/{rid}/chunks/2',files={'file':('chunk',payload[2000:])}).status_code==200
    with TestClient(create_app(root,start_worker=False)) as restarted:
        pending=restarted.get('/api/recordings').json()[0]
        assert pending['chunk_count']==3 and pending['saved_chunks']==2
        assert restarted.post(f'/api/recordings/{rid}/finish',json={'chunk_count':3}).status_code==409
        assert restarted.get('/api/meetings').json()==[]
        assert restarted.put(f'/api/recordings/{rid}/chunks/1',files={'file':('chunk',payload[1000:2000])}).status_code==200
        assert restarted.post(f'/api/recordings/{rid}/finish',json={'chunk_count':3}).status_code==200
        assert (restarted.app.state.store.directory(rid)/'original').read_bytes()==payload

def test_native_recording_recovery_retains_separate_aligned_tracks(client):
    import struct
    from app.recordings import RecordingStore
    store=client.app.state.store;recordings=RecordingStore(store)
    session=recordings.create('audio/wav');session['source']='macos'
    folder=recordings.folder(session['id']);RecordingStore.write(folder/'session.json',session)
    system=struct.pack('<h',1000)*16000
    microphone=b'\0\0'*8000+struct.pack('<h',2000)*8000
    (folder/'system.pcm').write_bytes(system);(folder/'microphone.pcm').write_bytes(microphone)
    RecordingStore.write(folder/'capture.json',{'state':'stopped','duration':1,'error':''})
    pending=client.get('/api/recordings').json()[0]
    assert pending['source']=='macos' and pending['native_audio_saved'] and pending['duration']==1
    notes=[{'captured_at':.5,'note_type':'highlight','raw_text':'两条原轨分别保留'}]
    client.put(f"/api/recordings/{session['id']}/metadata",json={'notes':notes,'duration':1,'chunk_count':0})
    response=client.post(f"/api/native-capture/{session['id']}/finish")
    assert response.status_code==200,response.text
    meeting=response.json();assert meeting['audio_tracks']==['system','microphone'] and len(meeting['notes'])==1
    for name,payload in [('system',system),('microphone',microphone)]:
        result=client.get(f"/api/meetings/{session['id']}/audio-tracks/{name}")
        assert result.status_code==200
        with wave.open(io.BytesIO(result.content),'rb') as wav:
            assert wav.getframerate()==16000 and wav.getnchannels()==1
            assert wav.readframes(wav.getnframes())==payload
    assert client.post(f"/api/native-capture/{session['id']}/finish").json()['id']==session['id']
    assert client.get('/api/recordings').json()==[]

def test_native_capture_unavailable_does_not_start_recording(client,monkeypatch):
    monkeypatch.setattr('app.native_capture.NativeCapture.available',lambda _:False)
    assert client.post('/api/native-capture',json={'microphone':True}).status_code==409
    assert client.get('/api/recordings').json()==[]

def test_native_capture_rejects_empty_audio_and_foreign_session(client):
    rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
    assert client.get(f'/api/native-capture/{rid}').status_code==409
    assert client.post(f'/api/native-capture/{rid}/finish').status_code==409

def test_timestamped_meeting_notes_lifecycle(client):
    mid=upload(client)
    result=client.post(f'/api/meetings/{mid}/notes/batch',json={'notes':[
        {'captured_at':12.5,'note_type':'highlight','raw_text':'  重点确认回款时间  '},
        {'captured_at':4,'note_type':'question','raw_text':'预算是否确定？'},
    ]})
    assert result.status_code==201 and result.json()['count']==2
    notes=client.get(f'/api/meetings/{mid}').json()['notes']
    assert [note['captured_at'] for note in notes]==[4,12.5]
    assert notes[1]['raw_text']=='重点确认回款时间'
    nid=notes[0]['id']
    assert client.put(f'/api/meetings/{mid}/notes/{nid}',json={
        'note_type':'action','raw_text':'确认预算负责人'}).status_code==200
    updated=client.get(f'/api/meetings/{mid}').json()['notes'][0]
    assert updated['note_type']=='action' and updated['raw_text']=='确认预算负责人'
    assert client.delete(f'/api/meetings/{mid}/notes/{nid}').status_code==200
    assert len(client.get(f'/api/meetings/{mid}').json()['notes'])==1

def test_incremental_live_transcription(client,monkeypatch):
    monkeypatch.setattr('app.main.WhisperCpp.ready',lambda _:True)
    monkeypatch.setattr('app.main.WhisperCpp.transcribe',lambda self,audio,output,run:[
        {'start':.25,'end':.9,'text':'这是实时草稿','speaker':'未分配'}])
    result=client.post('/api/live-transcribe',data={'offset':'15'},files={
        'file':('live.wav',wav_bytes(1),'audio/wav')})
    assert result.status_code==200
    assert result.json()['segments']==[{'start':15.25,'end':15.9,'text':'这是实时草稿'}]
    assert result.json()['duration']==1

def test_incremental_live_transcription_validates_audio(client,monkeypatch):
    monkeypatch.setattr('app.main.WhisperCpp.ready',lambda _:True)
    assert client.post('/api/live-transcribe',data={'offset':'0'},files={
        'file':('bad.wav',b'not-wave','audio/wav')}).status_code==400

def test_live_inference_keeps_other_requests_responsive(client,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event();release=threading.Event()
    monkeypatch.setattr('app.main.WhisperCpp.ready',lambda _:True)
    def transcribe(*args):
        entered.set();assert release.wait(5);return []
    monkeypatch.setattr('app.main.WhisperCpp.transcribe',transcribe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        task=pool.submit(client.post,'/api/live-transcribe',files={'file':('live.wav',wav_bytes(),'audio/wav')})
        try:
            assert entered.wait(2)
            other=pool.submit(client.get,'/api/meetings')
            assert other.result(timeout=2).status_code==200
            busy=client.post('/api/live-transcribe',files={'file':('live.wav',wav_bytes(),'audio/wav')})
            assert busy.status_code==409
        finally:release.set()
        assert task.result(timeout=3).status_code==200

def test_version_conflict_export_and_range(client):
    mid=upload(client)
    assert client.put(f'/api/meetings/{mid}/transcript',json={'revision':0,'segments':sample()}).json()['revision']==1
    assert client.put(f'/api/meetings/{mid}/transcript',json={'revision':0,'segments':sample()}).status_code==409
    edited=sample();edited[0]['text']='确定下周五交付。'
    assert client.put(f'/api/meetings/{mid}/transcript',json={'revision':1,'segments':edited}).status_code==200
    assert client.get(f'/api/meetings/{mid}/revisions/1').json()['segments']==sample()
    srt=client.get(f'/api/meetings/{mid}/export/srt')
    assert '00:00:00,000 --> 00:00:00,500' in srt.text
    assert '下周五' in srt.text
    assert srt.headers['content-disposition'].endswith(".srt")
    assert client.get(f'/api/meetings/{mid}/audio',headers={'Range':'bytes=0-15'}).status_code==206
    for fmt in ['txt','md','json']:
        exported=client.get(f'/api/meetings/{mid}/export/{fmt}')
        assert exported.status_code==200
        assert exported.headers['content-disposition'].endswith(f".{fmt}")
        assert '测试会议' in exported.text

def test_upload_validation_and_origin(client):
    assert client.post('/api/meetings',files={'file':('a.exe',b'x')}).status_code==400
    assert client.post('/api/meetings',files={'file':('a.wav',b'')}).status_code==400
    assert client.post('/api/meetings',headers={'origin':'https://evil.test'},files={'file':('a.wav',b'x')}).status_code==403
    assert client.get('/api/status',headers={'host':'evil.test'}).status_code==403
    assert client.get('/api/meetings').json()==[]

def test_duplicate_segments_and_invalid_times(client):
    mid=upload(client);s=sample()
    assert client.put(f'/api/meetings/{mid}/transcript',json={'revision':0,'segments':s+s}).status_code==400
    s[0]['end']=-1
    assert client.put(f'/api/meetings/{mid}/transcript',json={'revision':0,'segments':s}).status_code==422

def test_queue_cancel_retry_delete(client,monkeypatch):
    monkeypatch.setattr('app.main.WhisperCpp.ready',lambda _:True)
    mid=upload(client)
    r=client.post(f'/api/meetings/{mid}/jobs',json={});assert r.status_code==201
    jid=r.json()['id']
    assert client.post(f'/api/meetings/{mid}/jobs',json={}).status_code==409
    assert client.delete(f'/api/meetings/{mid}').status_code==409
    assert client.post(f'/api/jobs/{jid}/cancel').status_code==200
    assert client.post(f'/api/jobs/{jid}/retry').status_code==200
    client.post(f'/api/jobs/{jid}/cancel')
    assert client.delete(f'/api/meetings/{mid}').status_code==200
    assert client.get(f'/api/meetings/{mid}').status_code==404
    assert not client.app.state.store.directory(mid).exists()

def test_speaker_overlap_aggregation():
    turns=[{'start':0,'end':1,'speaker':'A'},{'start':1,'end':2.5,'speaker':'B'},{'start':2.5,'end':4,'speaker':'A'}]
    assert attach_speakers([{'start':0,'end':4,'speaker':'unknown'}],turns)[0]['speaker']=='A'

def test_checkpoint_reuse_preserves_manual_edit(client,monkeypatch):
    mid=upload(client);store=client.app.state.store;worker=client.app.state.worker
    jid=uid();config={'base_revision':0,'diarization':False,'punctuation':False,'pipeline_version':2}
    store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'running','test',encode(config)))
    folder=store.directory(mid)/jid;folder.mkdir();(folder/'audio.wav').write_bytes(wav_bytes())
    from app.job_checkpoints import JobCheckpoints,file_digest
    from app.providers import WhisperCpp
    checkpoints=JobCheckpoints(store,store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    source={'sha256':file_digest(store.directory(mid)/'original')}
    checkpoints.save_file('audio',folder/'audio.wav',source)
    import wave
    with wave.open(str(folder/'audio.wav')) as audio:duration=audio.getnframes()/audio.getframerate()
    checkpoints.save('chunk-0',sample(),[source,WhisperCpp().identity(),0,duration])
    monkeypatch.setattr('app.worker.WhisperCpp.ready',lambda _:True)
    monkeypatch.setattr('app.worker.WhisperCpp.transcribe',lambda *_:pytest.fail('should reuse checkpoint'))
    store.save_revision(mid,[dict(sample()[0],text='人工校对')],'manual',expected=0)
    worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    result=client.get(f'/api/meetings/{mid}').json()
    assert result['segments'][0]['text']=='人工校对'
    assert result['artifacts'][0]['kind']=='transcript_candidate'
    assert result['jobs'][0]['state']=='completed'

def test_cancelled_job_will_not_publish(client):
    mid=upload(client);store=client.app.state.store;jid=uid()
    store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'cancelled','test','{}'))
    with pytest.raises(Cancelled):client.app.state.worker.check(jid)
    assert client.get(f'/api/meetings/{mid}').json()['revision']==0

def test_future_analysis_citations_and_staleness(client):
    from app.analysis import AnalysisResult, AnalysisItem, save_analysis
    mid=upload(client);store=client.app.state.store
    store.save_revision(mid,sample(),'manual',expected=0)
    result=AnalysisResult(kind='action_items',items=[AnalysisItem(content='交付',source_segment_ids=['seg1'])])
    save_analysis(store,mid,1,result,{'model':'test','template_version':'1'})
    assert not client.get(f'/api/meetings/{mid}').json()['artifacts'][0]['stale']
    store.save_revision(mid,sample(),'manual',expected=1)
    assert client.get(f'/api/meetings/{mid}').json()['artifacts'][0]['stale']
    result.items[0].source_segment_ids=['missing']
    with pytest.raises(ValueError):save_analysis(store,mid,1,result,{})

def test_single_worker_lock_and_restart_requeues(client):
    from app.worker import Worker
    from app.db import Store
    worker=client.app.state.worker
    # Keep the actual loop idle while testing its recovery and lock behavior.
    worker.loop=lambda:None
    mid=upload(client);jid=uid()
    client.app.state.store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'running','test','{}'))
    worker.start()
    try:
        assert client.app.state.store.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']=='queued'
        other=Worker(Store(client.app.state.store.root))
        with pytest.raises(RuntimeError):other.start()
    finally: worker.close()

def test_different_boundary_segmentation_does_not_drop_words():
    from app.providers import merge_overlapping_chunks
    first=[{'start':297.91,'end':300.31,'text':'和你们一起走过很多很多的路'},
           {'start':300.31,'end':301.25,'text':'坚持到底'}]
    second=[{'start':298.35,'end':301.25,'text':'我们一起走过很多很多的路,坚持到底'},
            {'start':301.25,'end':309.04,'text':'下面播放下一条音频'}]
    result=merge_overlapping_chunks([first,second])
    assert ''.join(x['text'] for x in result)=='和你们一起走过很多很多的路坚持到底下面播放下一条音频'

def test_overlap_preserves_new_suffix_and_uncertain_text():
    from app.providers import merge_overlapping_chunks
    first=[{'start':0,'end':5,'text':'我们明天一起讨论'}]
    second=[{'start':4,'end':8,'text':'一起讨论，交付计划'}]
    result=merge_overlapping_chunks([first,second])
    assert result[1]['text']=='交付计划'
    uncertain=merge_overlapping_chunks([first,[{'start':4,'end':6,'text':'另外一个声音'}]])
    assert uncertain[1]['boundary_review']

def test_native_draft_uses_shared_transcriber_and_absolute_timeline(client,monkeypatch):
    rid=native_session(client)
    monkeypatch.setattr('app.main.WhisperCpp.ready',lambda _:True)
    monkeypatch.setattr('app.main.WhisperCpp.transcribe',lambda *args:[{'start':.25,'end':1,'text':'原生草稿'}])
    monkeypatch.setattr('app.main.NativeCapture.draft_window',lambda *args:{
        'ready':True,'payload':wav_bytes(15),'silent':False,'offset':45,'next_offset':60,'skipped_seconds':45})
    response=client.post(f'/api/native-capture/{rid}/draft',json={'offset':0})
    assert response.status_code==200,response.text
    result=response.json()
    assert [{k:part[k] for k in ('start','end','text')} for part in result['segments']]==[{'start':45.25,'end':46,'text':'原生草稿'}]
    assert result['segments'][0]['live_segment_index']==0
    assert client.app.state.store.one('SELECT recording_id FROM live_asr_ranges WHERE id=?',(result['segments'][0]['live_range_id'],))['recording_id']==rid
    assert result['next_offset']==60 and result['skipped_seconds']==45
    assert 'payload' not in result and client.get('/api/meetings').json()==[]

def test_native_draft_waiting_and_silence_do_not_invoke_model(client,monkeypatch):
    rid=native_session(client)
    def unexpected(*args):raise AssertionError('不应调用模型')
    monkeypatch.setattr('app.main.WhisperCpp.transcribe',unexpected)
    monkeypatch.setattr('app.main.NativeCapture.draft_window',lambda *args:{'ready':False,'next_offset':0,'skipped_seconds':0})
    assert client.post(f'/api/native-capture/{rid}/draft',json={}).json()['ready'] is False
    monkeypatch.setattr('app.main.NativeCapture.draft_window',lambda *args:{
        'ready':True,'payload':wav_bytes(15,silent=True),'silent':True,'offset':0,'next_offset':15,'skipped_seconds':0})
    result=client.post(f'/api/native-capture/{rid}/draft',json={}).json()
    assert result['segments']==[] and result['next_offset']==15
    assert 'payload' not in result

def test_native_draft_rejects_unknown_foreign_and_invalid_offsets(client):
    rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
    assert client.post(f'/api/native-capture/{rid}/draft',json={}).status_code==409
    unknown=uid()
    assert client.post(f'/api/native-capture/{unknown}/draft',json={}).status_code==404
    assert client.post(f'/api/native-capture/{rid}/draft',json={'offset':-1}).status_code==422

def test_native_draft_does_not_block_capture_stop(client,monkeypatch):
    rid=native_session(client)
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event();release=threading.Event()
    monkeypatch.setattr('app.main.WhisperCpp.ready',lambda _:True)
    monkeypatch.setattr('app.main.NativeCapture.draft_window',lambda *args:{
        'ready':True,'payload':wav_bytes(15),'silent':False,'offset':0,'next_offset':15,'skipped_seconds':0})
    def transcribe(*args):
        entered.set();assert release.wait(5);return []
    monkeypatch.setattr('app.main.WhisperCpp.transcribe',transcribe)
    monkeypatch.setattr('app.main.NativeCapture.command',lambda *args:{'state':'stopped','duration':15,'error':''})
    with ThreadPoolExecutor(max_workers=2) as pool:
        task=pool.submit(client.post,f'/api/native-capture/{rid}/draft',json={})
        try:
            assert entered.wait(2)
            stop=pool.submit(client.post,f'/api/native-capture/{rid}/command',json={'command':'stop'})
            assert stop.result(timeout=2).json()['state']=='stopped'
            assert client.post(f'/api/native-capture/{rid}/draft',json={}).status_code==409
        finally:release.set()
        assert task.result(timeout=3).status_code==200

def test_live_qwen_routes_to_draft_without_whisper_fallback(client,monkeypatch):
    monkeypatch.setattr('app.providers.QwenMLX.ready',lambda _:True)
    monkeypatch.setattr('app.providers.QwenMLX.transcribe_live',lambda *args:[{'start':0,'end':1,'text':'千问实时草稿'}])
    monkeypatch.setattr('app.main.WhisperCpp.transcribe',lambda *args:pytest.fail('不得回退 Whisper'))
    result=client.post('/api/live-transcribe',data={'offset':'15','engine':'qwen'},files={'file':('live.wav',wav_bytes(),'audio/wav')})
    assert result.status_code==200,result.text
    assert result.json()['engine']=='qwen'
    assert result.json()['segments']==[{'start':15,'end':16,'text':'千问实时草稿'}]
    assert client.post('/api/live-transcribe',data={'engine':'other'},files={'file':('live.wav',wav_bytes(),'audio/wav')}).status_code==422
    monkeypatch.setattr('app.providers.QwenMLX.ready',lambda _:False)
    response=client.post('/api/live-transcribe',data={'engine':'qwen'},files={'file':('live.wav',wav_bytes(),'audio/wav')})
    assert response.status_code==409 and '千问' in response.json()['detail']

def test_native_live_qwen_respects_selected_engine(client,monkeypatch):
    rid=native_session(client)
    monkeypatch.setattr('app.providers.QwenMLX.ready',lambda _:True)
    monkeypatch.setattr('app.providers.QwenMLX.transcribe_live',lambda *args:[{'start':0,'end':15,'text':'联合采集草稿'}])
    monkeypatch.setattr('app.main.NativeCapture.draft_window',lambda *args:{
        'ready':True,'payload':wav_bytes(15),'silent':False,'offset':30,'next_offset':45,'skipped_seconds':0})
    response=client.post(f'/api/native-capture/{rid}/draft',json={'offset':30,'engine':'qwen'})
    assert response.status_code==200,response.text
    assert response.json()['engine']=='qwen'
    assert response.json()['segments'][0]['start']==30
    assert client.post(f'/api/native-capture/{rid}/draft',json={'engine':'cloud'}).status_code==422


def test_inline_speaker_review_obeys_live_priority_before_inference(client,monkeypatch):
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample()+[dict(sample()[0],id='seg2',speaker='发言人 2',start=.5,end=1)],'manual',expected=0)
    scheduler=client.app.state.services.resources;calls=[]
    monkeypatch.setattr('app.main.LMStudioProvider.resolve_model',lambda *_:'synthetic')
    def review(*_):calls.append('model');return {'suggestions':[],'chunks':1}
    monkeypatch.setattr('app.main.generate_review',review)
    scheduler.set_realtime(['synthetic-session'])
    response=client.post(f'/api/meetings/{mid}/speaker-reviews',json={})
    assert response.status_code==409 and '会中识别优先' in response.json()['detail']
    assert not calls and not store.all('SELECT * FROM artifacts')
    scheduler.set_realtime([])
    assert client.post(f'/api/meetings/{mid}/speaker-reviews',json={}).status_code==201
    assert calls==['model'] and not scheduler.snapshot()['requests']
