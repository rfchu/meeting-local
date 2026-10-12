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

def test_abandoned_capture_stops_only_after_grace_and_keeps_audio(capture):
    value,folder=session(capture)
    rid=value['id'];process=Process(folder)
    capture.processes[rid]=process;capture.last_seen[rid]=time.monotonic()-120
    (folder/'system.pcm').write_bytes(b'\x01\x00'*16000)
    capture.stop_abandoned()
    assert process.commands==[]
    capture.last_seen[rid]=time.monotonic()-301
    capture.stop_abandoned()
    assert process.commands==['stop'] and rid not in capture.processes
    assert '5 分钟' in capture.status(rid)['error']
    restarted=NativeCapture(capture.store,capture.recordings)
    assert '5 分钟' in restarted.status(rid)['error']
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
