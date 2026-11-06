"""Synthetic heartbeat fault/recovery UI, with no microphone or model access.
PYTHONPATH=. .venv/bin/python scripts/recording_faults_ui_fixture.py --port 9876
"""
import argparse,asyncio,json,os,tempfile,time
import uvicorn
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import app.main as module
import app.onboarding as onboarding
from app.main import create_app
from app.native_capture import NativeCapture
from app.recordings import RecordingStore


class Capture(NativeCapture):
    def __init__(self,*args):super().__init__(*args);self.states={};self.stale=True;self.input_loss=False;self.file_stall=False;self.diagnostics=None;self.command_failure=False
    def capability(self):return {'available':True,'supported':True,'screen_permission':True,'microphone_permission':True}
    def start_monitor(self):pass
    def start(self,microphone=True):
        s=self.recordings.create('audio/wav');s.update(source='macos',include_microphone=False)
        folder=self.recordings.folder(s['id']);RecordingStore.write(folder/'session.json',s)
        (folder/'system.pcm').write_bytes(b'\1\0'*480000);self.states[s['id']]='recording';self.processes[s['id']]=None
        self.status(s['id']);return s
    def is_running(self,rid):return self.states.get(rid) in ('recording','paused')
    def status(self,rid,touch=True):
        session=self.recordings.read(rid);folder=self.recordings.folder(rid);path=folder/'capture.json'
        state=self.states.get(rid,'interrupted')
        value={'state':state,'duration':30,'error':'','system_frames':480000,'system_levels':[.02,.06,.01]*24,'buffer_age':{'system':20 if self.input_loss else 0}}
        if self.diagnostics is not None:value['diagnostics']=self.diagnostics
        RecordingStore.write(path,value)
        if self.file_stall:os.utime(folder/'system.pcm',(time.time()-30,)*2)
        if self.stale:os.utime(path,(time.time()-30,time.time()-30))
        value['health']=self.health(session,value,path);return value
    def command(self,rid,command):
        if self.command_failure and command=='pause':raise RuntimeError('合成控制确认失败')
        if command=='stop':self.recordings.event('capture.stop_requested',rid)
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
        health_mode={'value':'normal'};inventory_mode={'value':'normal'};command_mode={'value':'normal'}
        @app.middleware('http')
        async def health_response_fault(request,call_next):
            if request.method=='GET' and request.url.path.startswith('/api/recordings/') and request.url.path.endswith('/health'):
                if health_mode['value']=='unavailable':return JSONResponse({'detail':'合成状态连接失败'},status_code=503)
                if health_mode['value']=='delay':await asyncio.sleep(3)
            if request.method=='GET' and request.url.path=='/api/recordings':
                if inventory_mode['value']=='unavailable':return JSONResponse({'detail':'合成录音列表连接失败'},status_code=503)
                if inventory_mode['value']=='delay':await asyncio.sleep(3)
            if request.method=='POST' and request.url.path.startswith('/api/recordings/') and request.url.path.endswith('/commands') and command_mode['value']=='conflict':
                return JSONResponse({'detail':{'code':'version_conflict','message':'合成控制版本冲突：状态已变化'}},status_code=409)
            return await call_next(request)
        @app.post('/_fixture/command-mode')
        def set_command_mode(body:dict):
            if body.get('mode') not in ('normal','unconfirmed','conflict'):raise HTTPException(400,'未知合成模式')
            command_mode['value']=body['mode'];capture.command_failure=body['mode']=='unconfirmed';return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        @app.post('/_fixture/inventory-mode')
        def set_inventory_mode(body:dict):
            if body.get('mode') not in ('normal','unavailable','delay'):raise HTTPException(400,'未知合成模式')
            inventory_mode['value']=body['mode'];return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        @app.post('/_fixture/health-mode')
        def set_health_mode(body:dict):
            if body.get('mode') not in ('normal','unavailable','delay'):raise HTTPException(400,'未知合成模式')
            health_mode['value']=body['mode'];return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        @app.post('/_fixture/recover')
        def recover():
            capture.stale=False;capture.input_loss=False;capture.file_stall=False
            for rid in capture.states:os.utime(capture.recordings.folder(rid)/'system.pcm',None)
            for rid in capture.states:capture.status(rid)
            app.state.recording_runtime.observe()
            return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop()) # Keep fixture controls before static catch-all.
        @app.post('/_fixture/input-loss')
        def input_loss():
            capture.stale=False;capture.input_loss=True
            for rid in capture.states:capture.status(rid)
            app.state.recording_runtime.observe()
            return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        @app.post('/_fixture/file-stall')
        def file_stall():
            capture.stale=False;capture.input_loss=False;capture.file_stall=True
            for rid in capture.states:capture.status(rid)
            app.state.recording_runtime.observe()
            return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        @app.post('/_fixture/native-diagnostics')
        def native_diagnostics():
            capture.diagnostics={'version':1,'helper_build':2,'default_input_changes':0,'default_output_changes':0,
                'audio_device_connections':0,'audio_device_disconnections':0,'listener_failures':0,'device_query_failures':0,
                'configured_microphone_is_default':None,'failure_stage':'none','stream_error_code':None,
                'tracks':{name:dict.fromkeys(('input_buffers','empty_outputs','dropped_buffers','format_changes','gap_frames'),0) for name in ('system','microphone')}}
            for rid in capture.states:capture.status(rid)
            return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        @app.post('/_fixture/native-change')
        def native_change():
            capture.diagnostics['default_input_changes']+=1
            capture.diagnostics['tracks']['system'].update(input_buffers=100,dropped_buffers=2,format_changes=1,gap_frames=3200)
            for rid in capture.states:capture.status(rid)
            app.state.recording_validation.sample()
            return {'ok':True}
        app.router.routes.insert(0,app.router.routes.pop())
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)

if __name__=='__main__':main()
