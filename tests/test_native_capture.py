import json
import os
import time
import pytest
from app.db import Store
from app.recordings import RecordingStore
from app.native_capture import NativeCapture

@pytest.fixture
def capture(tmp_path):
    store=Store(tmp_path)
    return NativeCapture(store,RecordingStore(store))

def session(capture,**status):
    value=capture.recordings.create('audio/wav')
    value.update(source='macos',include_microphone=True)
    folder=capture.recordings.folder(value['id'])
    RecordingStore.write(folder/'session.json',value)
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':12,'error':'',**status})
    return value,folder

def test_track_health_distinguishes_silence_missing_data_and_pause(capture):
    value,folder=session(capture,system_frames=192000,microphone_frames=0,
        buffer_age={'system':.1,'microphone':12},silence_seconds={'system':12})
    path=folder/'capture.json';status=json.loads(path.read_text())
    health=capture.health(value,status,path)
    assert [t['state'] for t in health['tracks']]==['quiet','no_data']
    status['silence_seconds']['system']=0
    assert capture.health(value,status,path)['tracks'][0]['state']=='receiving'
    status['state']='paused'
    assert all(t['state']=='paused' for t in capture.health(value,status,path)['tracks'])
    value['include_microphone']=False
    assert len(capture.health(value,status,path)['tracks'])==1

def test_stale_heartbeat_is_warning_not_automatic_stop(capture):
    value,folder=session(capture,system_frames=192000)
    path=folder/'capture.json';os.utime(path,(time.time()-20,time.time()-20))
    health=capture.health(value,json.loads(path.read_text()),path)
    assert health['stalled'] and health['tracks'][0]['state']=='stalled'
    assert json.loads(path.read_text())['state']=='recording'

def test_dead_helper_marks_saved_audio_recoverable(capture):
    value,folder=session(capture,system_frames=192000)
    (folder/'system.pcm').write_bytes(b'\x01\x00'*16000)
    status=capture.status(value['id'])
    assert status['state']=='interrupted'
    assert (folder/'system.pcm').stat().st_size==32000

def test_observed_helper_exit_is_persisted_once_without_guessing_cause(capture):
    from unittest.mock import Mock
    value,folder=session(capture);rid=value['id']
    process=Mock();process.poll.return_value=-9;capture.processes[rid]=process
    (folder/'system.pcm').write_bytes(b'audio')
    status=capture.status(rid)
    assert status['state']=='interrupted' and '-9' in status['error']
    assert '内存' not in status['error'] and '休眠' not in status['error']
    path=folder/'capture-exit.json';before=path.read_bytes()
    capture.stop_abandoned();capture.status(rid)
    assert before==path.read_bytes() and json.loads(before)['exit_code']==-9
    assert (folder/'system.pcm').read_bytes()==b'audio'

def test_stale_capture_preserves_duration_and_does_not_stop_live_helper(capture):
    rid,folder=running_window(capture,duration=60)
    path=folder/'capture.json';os.utime(path,(time.time()-90,time.time()-90))
    status=capture.status(rid)
    assert status['state']=='recording' and status['duration']==60
    assert status['health']['stalled'] and status['health']['heartbeat_age_seconds']>=90
    assert capture.processes[rid].commands==[]

def test_restart_marks_unowned_recording_interrupted_without_restarting_capture(capture):
    value,folder=session(capture);rid=value['id'];audio=b'preserved'
    (folder/'system.pcm').write_bytes(audio)
    capture.reconcile_interrupted()
    restored=capture.recordings.read(rid)
    assert restored['state']=='interrupted' and '未重新开始' in restored['stop_reason']
    assert not capture.processes and (folder/'system.pcm').read_bytes()==audio
    before=(folder/'session.json').read_bytes();capture.reconcile_interrupted()
    assert (folder/'session.json').read_bytes()==before

def test_restart_reconciliation_never_changes_owned_live_capture(capture):
    rid,folder=running_window(capture)
    before=(folder/'session.json').read_bytes()
    capture.reconcile_interrupted()
    assert before==(folder/'session.json').read_bytes()

class Process:
    def __init__(self,folder):
        self.folder=folder;self.stdin=self;self.stopped=False;self.commands=[]
    def poll(self):return 0 if self.stopped else None
    def write(self,payload):
        command=payload.decode().strip();self.commands.append(command)
        if command in ('pause','resume'):
            RecordingStore.write(self.folder/'capture.json',{'state':'paused' if command=='pause' else 'recording','duration':12,'error':''})
    def flush(self):pass
    def close(self):pass
    def wait(self,timeout):
        self.stopped=True
        RecordingStore.write(self.folder/'capture.json',{'state':'stopped','duration':12,'error':''})

def test_background_capture_continues_after_page_grace_and_keeps_audio(capture):
    value,folder=session(capture)
    rid=value['id'];process=Process(folder)
    capture.processes[rid]=process;capture.last_seen[rid]=time.monotonic()-120
    (folder/'system.pcm').write_bytes(b'\x01\x00'*16000)
    capture.stop_abandoned()
    assert process.commands==[]
    capture.last_seen[rid]=time.monotonic()-301
    capture.stop_abandoned()
    assert process.commands==[] and rid in capture.processes
    assert rid in capture.detached
    capture.status(rid,touch=False)
    assert rid in capture.detached  # ASR does not pretend the page returned.
    capture.status(rid)
    assert rid not in capture.detached
    assert (folder/'system.pcm').is_file()

def test_completed_deleted_native_recording_cannot_republish(capture):
    value,folder=session(capture)
    value['state']='completed';RecordingStore.write(folder/'session.json',value)
    (folder/'system.pcm').write_bytes(b'\x01\x00'*16000)
    with pytest.raises(ValueError,match='已完成并被删除'):capture.finish(value['id'])


def test_pause_resume_and_service_shutdown_preserve_raw_tracks(capture):
    value,folder=session(capture)
    rid=value['id'];process=Process(folder)
    capture.processes[rid]=process;capture.last_seen[rid]=time.monotonic()
    audio=b'\x01\x00'*16000;(folder/'system.pcm').write_bytes(audio)
    assert capture.command(rid,'pause')['state']=='paused'
    assert capture.command(rid,'resume')['state']=='recording'
    capture.close()
    assert process.commands==['pause','resume','stop']
    assert (folder/'system.pcm').read_bytes()==audio

def running_window(capture,duration=15,microphone=True,state='recording'):
    value,folder=session(capture,duration=duration,system_frames=round(duration*16000),
                         microphone_frames=round(duration*16000) if microphone else 0,state=state)
    value['include_microphone']=microphone;RecordingStore.write(folder/'session.json',value)
    process=Process(folder);capture.processes[value['id']]=process
    capture.last_seen[value['id']]=time.monotonic()
    return value['id'],folder

def test_draft_snapshot_mixes_aligned_tracks_without_mutating_originals(capture):
    import struct,wave,io
    rid,folder=running_window(capture)
    system=struct.pack('<h',20000)*240000
    microphone=b'\0\0'*80000+struct.pack('<h',20000)*160000
    (folder/'system.pcm').write_bytes(system);(folder/'microphone.pcm').write_bytes(microphone)
    result=capture.draft_window(rid,0)
    assert result['ready'] and result['offset']==0 and result['next_offset']==15 and not result['silent']
    with wave.open(io.BytesIO(result['payload']),'rb') as source:
        assert source.getframerate()==16000 and source.getnframes()==240000
        output=source.readframes(240000)
    assert output[:160000]==system[:160000]
    assert output[160000:]==struct.pack('<h',32767)*160000
    assert (folder/'system.pcm').read_bytes()==system
    assert (folder/'microphone.pcm').read_bytes()==microphone
    retry=capture.draft_window(rid,0)
    assert retry['payload']==result['payload'] and retry['next_offset']==15

def test_draft_backlog_skips_only_draft_not_recording(capture):
    rid,folder=running_window(capture,60,microphone=False)
    (folder/'system.pcm').write_bytes(b'\x01\0'*960000)
    result=capture.draft_window(rid,0)
    assert result['skipped_seconds']==45 and result['offset']==45 and result['next_offset']==60
    assert (folder/'system.pcm').stat().st_size==1920000

def test_draft_waits_for_complete_window_and_flushes_paused_tail(capture):
    rid,folder=running_window(capture,2,microphone=False)
    (folder/'system.pcm').write_bytes(b'\0\0'*32000)
    assert capture.draft_window(rid,0)=={'ready':False,'next_offset':0,'skipped_seconds':0}
    RecordingStore.write(folder/'capture.json',{'state':'paused','duration':2,'system_frames':32000,'error':''})
    result=capture.draft_window(rid,.5)
    assert result['ready'] and result['silent'] and result['next_offset']==2
    assert not capture.draft_window(rid,2)['ready']

def test_draft_horizon_uses_reported_frames_not_elapsed_clock(capture):
    rid,folder=running_window(capture,30,microphone=False)
    (folder/'system.pcm').write_bytes(b'\1\0'*16000)
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':30,'system_frames':16000,'error':''})
    assert not capture.draft_window(rid,0)['ready']
    for offset in (-1,float('inf'),float('nan')):
        with pytest.raises(ValueError):capture.draft_window(rid,offset)
    RecordingStore.write(folder/'capture.json',{'state':'stopped','duration':30,'error':''})
    with pytest.raises(ValueError,match='已经停止'):capture.draft_window(rid,0)

def test_native_responsive_previews_extend_same_unconfirmed_range(capture):
    rid,folder=running_window(capture,10,microphone=False)
    (folder/'system.pcm').write_bytes(b'\1\0'*160000)
    first=capture.draft_window(rid,0,5,True)
    second=capture.draft_window(rid,0,10,True)
    assert first['offset']==second['offset']==0
    assert first['next_offset']==5 and second['next_offset']==10
    assert not first['confirmed'] and not second['confirmed']
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':10,'system_frames':160000,'silence_seconds':{'system':1},'error':''})
    final=capture.draft_window(rid,0,15,True)
    assert final['confirmed'] and final['next_offset']==10
    RecordingStore.write(folder/'capture.json',{'state':'paused','duration':10,'system_frames':160000,'error':''})
    assert capture.draft_window(rid,0,15,True)['confirmed']

def test_native_short_utterance_uses_real_clock_silence_and_keeps_recording_tail(capture):
    import io,wave
    rid,folder=running_window(capture,5,microphone=False)
    prefix=b'\1\0'*16000;(folder/'system.pcm').write_bytes(prefix)
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':5,'system_frames':16000,'silence_seconds':{'system':4},'error':''})
    result=capture.draft_window(rid,0,5,True)
    assert result['confirmed'] and result['next_offset']==5
    with wave.open(io.BytesIO(result['payload']),'rb') as wav:
        assert wav.getnframes()==80000 and wav.readframes(80000)==prefix+b'\0\0'*64000
    capture.processes.pop(rid)
    RecordingStore.write(folder/'capture.json',{'state':'stopped','duration':5,'system_frames':16000,'error':''})
    capture.finish(rid)
    with wave.open(str(capture.store.directory(rid)/'system.wav'),'rb') as wav:
        assert wav.getnframes()==80000 and wav.readframes(80000)==prefix+b'\0\0'*64000

def test_managed_native_window_extends_to_pause_or_25_seconds(capture):
    from app.live_session import pcm_wav
    rid,folder=running_window(capture,16,microphone=False)
    voice=b'\x70\x17'
    (folder/'system.pcm').write_bytes(voice*16*16000)
    preview=capture.draft_window(rid,0,16,True,True)
    assert not preview['confirmed'] and preview['extending'] and preview['next_offset']==16
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':25,'system_frames':25*16000,'error':''})
    (folder/'system.pcm').write_bytes(voice*25*16000)
    hard=capture.draft_window(rid,0,25,True,True)
    assert hard['confirmed'] and hard['next_offset']==25 and hard['boundary_reason']=='hard_limit'
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':18.8,'system_frames':round(18.8*16000),'error':''})
    (folder/'system.pcm').write_bytes(voice*18*16000+b'\0\0'*round(.8*16000))
    natural=capture.draft_window(rid,0,20,True,True)
    # The next preview target can be later than the quiet tail; capture metadata wakes it early.
    if not natural['ready']:
        RecordingStore.write(folder/'capture.json',{'state':'recording','duration':18.8,'system_frames':round(18.8*16000),'silence_seconds':{'system':.8},'error':''})
        natural=capture.draft_window(rid,0,20,True,True)
    assert natural['confirmed'] and natural['next_offset']==18.8 and natural['boundary_reason']=='silence'

def test_managed_native_sentence_hint_uses_short_pause_before_preview_target(capture):
    rid,folder=running_window(capture,5.5,microphone=False)
    (folder/'system.pcm').write_bytes(b'\x70\x17'*5*16000+b'\0\0'*8000)
    assert not capture.draft_window(rid,0,8,True,True)['ready']
    result=capture.draft_window(rid,0,8,True,True,sentence_end=5)
    assert result['confirmed'] and result['boundary_reason']=='sentence_pause'
    assert result['next_offset']==5.4
    # Skipping a backlog must discard the old window's punctuation hint.
    RecordingStore.write(folder/'capture.json',{'state':'recording','duration':80,'system_frames':80*16000,'error':''})
    (folder/'system.pcm').write_bytes(b'\x70\x17'*60*16000+b'\0\0'*8000+b'\x70\x17'*round(19.5*16000))
    result=capture.draft_window(rid,0,25,True,True,sentence_end=5)
    assert result['offset']==55 and result['next_offset']==80
    assert result['boundary_reason']=='hard_limit'
