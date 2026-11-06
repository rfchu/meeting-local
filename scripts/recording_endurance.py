"""Synthetic PCM endurance runner. Never accesses devices, models or production data.

Default: three hours of wall-clock pacing. --speed 0 explicitly accelerates time.
Reports distinguish simulated audio duration from measured wall time.
"""
import argparse, hashlib, json, os, sys, tempfile, time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Store
from app.recordings import RecordingStore
from app.native_capture import NativeCapture
from app.recording_health import RecordingHealth
from app.recording_runtime import RecordingRuntime
from app.lifecycle import Lifecycle


class SyntheticProcess:
    def __init__(self):self.exited=False
    def poll(self):return 0 if self.exited else None


def run(seconds=10800,speed=1,step=15):
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='tingji-endurance-') as directory:
        root=Path(directory);store=Store(root);recordings=RecordingStore(store)
        journal=Lifecycle(root);recordings.lifecycle=journal
        native=NativeCapture(store,recordings)
        runtime=RecordingRuntime(store,recordings,native,RecordingHealth(store,recordings,native),journal)
        session=recordings.create('audio/wav');rid=session['id'];folder=recordings.folder(rid)
        session.update(source='macos',include_microphone=True)
        recordings.write(folder/'session.json',session)
        process=SyntheticProcess();native.processes[rid]=process
        files={name:(folder/(name+'.pcm')).open('w+b') for name in ('system','microphone')}
        elapsed=0;checks=0;bytes_before_pause=None
        prefix=b'\1\0'*16000
        try:
            while elapsed<seconds:
                delta=min(step,seconds-elapsed);elapsed+=delta
                # A full input loss, then automatic input recovery, with system audio continuing.
                lost=seconds*.3<=elapsed<seconds*.6
                for name,file in files.items():
                    if name=='microphone' and lost:continue
                    file.seek((elapsed-delta)*32000)
                    file.write(b'\1\0'*(delta*16000));file.flush();os.fsync(file.fileno())
                value={'state':'recording','duration':elapsed,'error':'',
                       'system_frames':elapsed*16000,'microphone_frames':files['microphone'].tell()//2,
                       'buffer_age':{'system':0,'microphone':20 if lost else 0}}
                recordings.write(folder/'capture.json',value)
                # Simulate a page suspended for longer than its former five-minute lease.
                native.last_seen[rid]=time.monotonic()-600
                native.stop_abandoned();assert process.poll() is None
                runtime.observe();view=runtime.read(rid)
                assert view['phase']=='recording' and view['storage']['last_saved_seconds']==elapsed
                mic=next(t for t in view['capture']['tracks'] if t['name']=='microphone')
                assert mic['state']==('no_data' if lost and elapsed>=10 else 'receiving')
                if elapsed>=seconds*.65 and bytes_before_pause is None:
                    bytes_before_pause=[f.stat().st_size for f in folder.glob('*.pcm')]
                    value['state']='paused';recordings.write(folder/'capture.json',value)
                    runtime.observe();assert runtime.read(rid)['phase']=='paused'
                    assert bytes_before_pause==[f.stat().st_size for f in folder.glob('*.pcm')]
                    value['state']='recording';recordings.write(folder/'capture.json',value)
                native.status(rid,touch=False);assert rid in native.detached
                checks+=1
                if speed:time.sleep(delta/speed)
            native.status(rid);assert rid not in native.detached
            # Read a late ASR window without loading the complete meeting into memory.
            result=native.draft_window(rid,max(0,seconds-15))
            assert result['ready'] and len(result['payload'])<=480044
            for file in files.values():file.seek(0);assert hashlib.sha256(file.read(32000)).digest()==hashlib.sha256(prefix).digest()
            # A stale heartbeat is unknown; a fresh report recovers the same process.
            path=folder/'capture.json';os.utime(path,(time.time()-90,)*2)
            assert runtime.read(rid)['phase']=='unknown'
            os.utime(path,None);assert runtime.read(rid)['phase']=='recording'
            process.exited=True;native.observe_exit(rid)
            assert runtime.read(rid)['phase']=='interrupted'
            assert RecordingHealth(store,recordings,native).snapshot(rid)['audio']['recoverable']
            events=journal.events(rid,10000)
            return {'passed':True,'source':'synthetic_pcm_no_devices','audio_timeline_seconds':seconds,
                    'wall_seconds':round(time.monotonic()-started,3),'speed':speed,'checks':checks,
                    'saved_bytes':sum(p.stat().st_size for p in folder.glob('*.pcm')),
                    'event_count':len(events),'input_loss_observed':any(e['code']=='microphone_input.no_data' for e in events),
                    'hardware_capture_validated':False,'sleep_wake_validated':False}
        finally:
            for file in files.values():file.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=10800)
    parser.add_argument('--speed',type=float,default=1)
    parser.add_argument('--step',type=int,default=15)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if not 60<=args.seconds<=43200 or args.speed<0 or not 1<=args.step<=15:parser.error('invalid duration, speed or step')
    report=run(args.seconds,args.speed,args.step)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
