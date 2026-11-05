"""UI checks with synthetic native input: never accesses microphone or models.
PYTHONPATH=. .venv/bin/python scripts/onboarding_ui_fixture.py --port 9876
"""
import argparse,io,json,tempfile,math
from pathlib import Path
from array import array
from app.main import create_app
import app.main as module
import app.onboarding as onboarding
from scripts.recording_ui_fixture import SyntheticCapture

class FakeProbe:
    def __init__(self,args,**_):
        self.stdin=io.BytesIO();self.code=None
        folder=Path(args[args.index('--output')+1])
        signal=array('h',(round(300*math.sin(i*2*math.pi*440/16000)) for i in range(16000*10)))
        (folder/'system.pcm').write_bytes(signal.tobytes())
        (folder/'capture.json').write_text(json.dumps({'state':'recording','duration':10}))
    def poll(self):return self.code
    def wait(self,timeout=None):self.code=0
    def terminate(self):self.code=-15
    def kill(self):self.code=-9

def main():
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=9876);args=parser.parse_args()
    module.NativeCapture=SyntheticCapture
    catalog=lambda:[{'id':'qwen','label':'Qwen-ASR（测试未就绪）','ready':False},{'id':'whisper','label':'Whisper（测试未就绪）','ready':False}]
    module.engine_catalog=catalog;onboarding.engine_catalog=catalog
    module.LMStudioProvider.status=lambda self:{'ready':False,'models':[],'error':'样例环境不连接模型','profiles':[]}
    with tempfile.TemporaryDirectory(prefix='tingji-onboarding-ui-') as workspace:
        app=create_app(workspace,start_worker=False)
        app.state.capture_probes.process_factory=FakeProbe
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)

if __name__=='__main__':main()
