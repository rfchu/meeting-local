"""Isolated UI acceptance fixture: synthetic audio only, no microphone or user database.
Run from the repository root: PYTHONPATH=. .venv/bin/python scripts/recording_ui_fixture.py
"""
import tempfile
from pathlib import Path
from app.main import create_app
import app.main as module
from app.native_capture import NativeCapture
from app.recordings import RecordingStore
from app.db import Store

class SyntheticCapture(NativeCapture):
    def __init__(self,*args):super().__init__(*args);self.states={}
    def capability(self):return {'available':True,'supported':True,'screen_permission':True,'microphone_permission':True,'build_command':'synthetic-test'}
    def start_monitor(self):pass
    def close(self):self.states.clear()
    def is_running(self,rid):return self.states.get(rid) in ('recording','paused')
    def start(self,microphone=True):
        session=self.recordings.create('audio/wav');session.update(source='macos',include_microphone=False)
        folder=self.recordings.folder(session['id']);self.recordings.write(folder/'session.json',session)
        (folder/'system.pcm').write_bytes(b'\0\0'*16000*12)
        self.states[session['id']]='recording';self.write_status(session['id']);return session
    def write_status(self,rid):
        status={'state':self.states.get(rid,'interrupted'),'duration':12,'error':'','system_frames':192000,'system_levels':[.02,.05,.1,.04]*18,'microphone_levels':[]}
        self.recordings.write(self.recordings.folder(rid)/'capture.json',status);return status
    def status(self,rid):
        status=self.write_status(rid)
        status['health']={'stalled':False,'tracks':[{'name':'system','state':'paused' if status['state']=='paused' else 'receiving','saved_seconds':12}]}
        return status
    def command(self,rid,command):self.states[rid]={'pause':'paused','resume':'recording','stop':'stopped'}[command];return self.status(rid)
    def draft_window(self,*args,**kwargs):return {'ready':False}

def main():
    import argparse
    import uvicorn
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tingji-reliability-ui-') as workspace:
        root=Path(workspace)
        store=Store(root);recordings=RecordingStore(store)
        session=recordings.create('audio/webm')
        recordings.metadata(session['id'],[{'captured_at':4,'raw_text':'验证用简记：核对中断后恢复','note_type':'note','note_scope':'personal'}],15,3)
        recordings.chunk(session['id'],0,b'fixture-head');recordings.chunk(session['id'],2,b'fixture-tail')
        previous=module.NativeCapture
        try:
            module.NativeCapture=SyntheticCapture
            app=create_app(root,start_worker=False)
        finally:module.NativeCapture=previous
        app.state.keep_awake.supported=False
        print('SYNTHETIC TEST ONLY — no microphone, no real capture; temporary data removed on exit.',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
