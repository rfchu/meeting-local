"""Synthetic heartbeat fault/recovery UI, with no microphone or model access.
PYTHONPATH=. .venv/bin/python scripts/recording_faults_ui_fixture.py --port 9876
"""
import argparse,json,os,tempfile,time
import uvicorn
import app.main as module
import app.onboarding as onboarding
from app.main import create_app
from app.native_capture import NativeCapture
from app.recordings import RecordingStore


class Capture(NativeCapture):
    def __init__(self,*args):super().__init__(*args);self.states={};self.stale=True
    def capability(self):return {'available':True,'supported':True,'screen_permission':True,'microphone_permission':True}
    def start_monitor(self):pass
    def start(self,microphone=True):
        s=self.recordings.create('audio/wav');s.update(source='macos',include_microphone=False)
        folder=self.recordings.folder(s['id']);RecordingStore.write(folder/'session.json',s)
        (folder/'system.pcm').write_bytes(b'\1\0'*480000);self.states[s['id']]='recording'
        self.status(s['id']);return s
    def is_running(self,rid):return self.states.get(rid) in ('recording','paused')
    def status(self,rid,touch=True):
        session=self.recordings.read(rid);folder=self.recordings.folder(rid);path=folder/'capture.json'
        state=self.states.get(rid,'interrupted')
        value={'state':state,'duration':30,'error':'','system_frames':480000,'system_levels':[.02,.06,.01]*24}
        RecordingStore.write(path,value)
        if self.stale:os.utime(path,(time.time()-30,time.time()-30))
        value['health']=self.health(session,value,path);return value
    def command(self,rid,command):
        self.states[rid]={'stop':'stopped','pause':'paused','resume':'recording'}[command]
        return self.status(rid)
    def close(self):pass


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=9876);args=parser.parse_args()
    capture=None
    def factory(*values):
        nonlocal capture
        capture=Capture(*values);return capture
    module.NativeCapture=factory;module.engine_catalog=lambda:[];onboarding.engine_catalog=lambda:[]
    module.LMStudioProvider.status=lambda self:{'ready':False,'models':[],'error':'合成环境不调用模型','profiles':[]}
    with tempfile.TemporaryDirectory(prefix='recording-faults-ui-') as root:
        app=create_app(root,start_worker=False);app.state.keep_awake.supported=False
        @app.post('/_fixture/recover')
        def recover():
            capture.stale=False
            for rid in capture.states:capture.status(rid)
            return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop()) # Keep the fixture control before the static catch-all.
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)

if __name__=='__main__':main()
