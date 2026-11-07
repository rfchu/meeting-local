"""Explicit opt-in real-device smoke check. Audio stays in a new isolated directory.

No ASR, no permission prompts, no service stop. For synthetic testing use recording_endurance.py.
"""
import argparse,json,os,sys,time,wave,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.db import Store
from app.recordings import RecordingStore
from app.native_capture import NativeCapture
from app.recording_health import RecordingHealth
from app.recording_runtime import RecordingRuntime
from app.recording_validation import RecordingValidation
from app.lifecycle import Lifecycle
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real-device',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=60)
    parser.add_argument('--microphone',action='store_true')
    args=parser.parse_args()
    if not args.run_real_device:parser.error('真实设备录音需显式指定 --run-real-device；仅检查说明不会启动设备')
    if not 60<=args.seconds<=300:parser.error('短测须为 60～300 秒；长录音使用工作台与观察工具')
    from app.paths import RuntimePaths
    from app.capture_ownership import inspect
    from app.native_capture import HELPER
    import subprocess
    root=args.output.expanduser().resolve();formal=RuntimePaths.from_env().data.resolve()
    if root.exists() or root==formal or root.is_relative_to(formal) or formal.is_relative_to(root):parser.error('输出须为新的隔离目录，不能覆盖正式数据')
    if inspect(formal/'recordings')['lease']!='free':parser.error('正式采集仍在使用或状态不明，未启动短测')
    permission=json.loads(subprocess.check_output([str(HELPER),'--probe'],text=True,timeout=5))
    if not permission.get('screen_permission') or (args.microphone and not permission.get('microphone_permission')):parser.error('现有设备权限不足，请先在工作台配置；短测不会申请新权限')
    root.mkdir(parents=True,mode=0o700)
    os.environ['TINGJI_CAPTURE_CONTROL_ROOT']='/private/tmp/tj8-'+uuid.uuid4().hex[:12]
    store=Store(root);recordings=RecordingStore(store);journal=Lifecycle(root);recordings.lifecycle=journal
    native=NativeCapture(store,recordings);runtime=RecordingRuntime(store,recordings,native,RecordingHealth(store,recordings,native),journal)
    checks=RecordingValidation(root,recordings,native,runtime)
    rid=None
    try:
        session=native.start(microphone=args.microphone);rid=session['id'];folder=recordings.folder(rid)
        deadline=time.monotonic()+15
        while native.status(rid,touch=False)['state']=='starting' and time.monotonic()<deadline:time.sleep(.3)
        state=native.status(rid,touch=False)
        if state['state']!='recording':raise RuntimeError('设备采集未进入录音状态，请检查本机权限和测试日志')
        run=checks.start(rid,args.seconds);(root/'run.json').write_text(json.dumps({'recording_id':rid,'check_id':run['id']}))
        print('REAL DEVICE RECORDING STARTED: bounded capture, selected tracks, no ASR',flush=True)
        for n in range((args.seconds+4)//5):
            time.sleep(min(5,args.seconds-n*5));checks.sample();status=native.status(rid,touch=False)
            print(json.dumps({'elapsed':min((n+1)*5,args.seconds),'state':status['state'],'duration':status.get('duration'),'bytes':{name:(folder/(name+'.pcm')).stat().st_size if (folder/(name+'.pcm')).exists() else 0 for name in ('system','microphone')}}),flush=True)
        (root/'observation.json').write_text(json.dumps(checks.report(run['id']),ensure_ascii=False,indent=2))
        stop_run=checks.start(rid,60)
        print('STOP REQUEST',native.command(rid,'stop')['state'],flush=True)
        time.sleep(1);checks.sample()
        (root/'stop-observation.json').write_text(json.dumps(checks.report(stop_run['id']),ensure_ascii=False,indent=2))
        for name in ('system','microphone'):
            path=folder/(name+'.pcm')
            if path.exists():
                with wave.open(str(root/(name+'.wav')),'wb') as output:
                    output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(path.read_bytes())
        print(json.dumps({'state':native.status(rid,touch=False)['state'],'evidence_directory':str(root),'probe':'real_device_no_asr'}),flush=True)
    finally:
        if rid and native.is_running(rid):
            try:native.command(rid,'stop')
            except (OSError,ValueError):pass
        checks.close();native.close()

if __name__=='__main__':main()
