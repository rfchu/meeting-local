"""E6 synthetic UI: real detached broker + device-free Swift test writer, no inference.
The writer must be built from native/BrokerWriterTests with Ownership.swift.
"""
import argparse,json,os,subprocess,sys,tempfile,time,uuid
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--writer',type=Path,required=True);parser.add_argument('--static-dir',type=Path,required=True);parser.add_argument('--port',type=int,default=9876);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tj6-ui-',dir='/private/tmp') as temporary:
        root=Path(temporary);os.environ['MEETING_DATA']=str(root/'data');os.environ['TINGJI_CAPTURE_CONTROL_ROOT']=str(root/'control')
        from app.main import create_app
        from app.capture_broker import Client
        from app.providers import WhisperCpp,QwenMLX
        from app.analysis import LMStudioProvider
        from unittest.mock import patch
        import uvicorn
        with patch.object(WhisperCpp,'ready',lambda _:False),patch.object(QwenMLX,'ready',lambda _:False),patch.object(LMStudioProvider,'status',lambda _:{'ready':False,'models':[],'error':'synthetic','base_url':''}):
            app=create_app(root/'data',start_worker=False);app.state.keep_awake.supported=False
            recordings=app.state.services.recordings;session=recordings.create('audio/wav')
            session.update(source='macos',include_microphone=False,capture_incarnation=uuid.uuid4().hex,capture_transport='broker_v2')
            recordings.write(recordings.folder(session['id'])/'session.json',session)
            script="from app.capture_broker import Broker;import sys;Broker(sys.argv[1],sys.argv[2],sys.argv[3],[sys.argv[4],'--output',sys.argv[5],'--incarnation',sys.argv[3]],awake_enabled=False).run()"
            process=subprocess.Popen([sys.executable,'-c',script,str(recordings.root),session['id'],session['capture_incarnation'],str(args.writer),str(recordings.folder(session['id']))],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            try:
                client=Client(recordings.root,session['id'],session['capture_incarnation'])
                until=time.monotonic()+8
                while time.monotonic()<until:
                    try:
                        if client.status()['alive'] and __import__('app.capture_ownership',fromlist=['inspect']).inspect(recordings.root)['lease']=='held':break
                    except (OSError,ValueError):pass
                    time.sleep(.05)
                else:raise RuntimeError('synthetic broker startup failed')
                static=next(route for route in app.routes if getattr(route,'path',None)=='').app
                static.directory=str(args.static_dir);static.all_directories=[str(args.static_dir)]
                print('E6 SYNTHETIC BROKER UI ONLY: no devices or inference',flush=True)
                uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)
                # EOF closes the test writer; it never accesses real audio devices.
                from app.capture_ownership import inspect
                until=time.monotonic()+5
                while time.monotonic()<until and inspect(recordings.root)['lease']!='free':time.sleep(.03)

if __name__=='__main__':main()
