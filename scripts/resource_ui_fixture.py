"""C1 synthetic resource UI: PYTHONPATH=. .venv/bin/python scripts/resource_ui_fixture.py.
No models, microphone or user data. Scenario controls exist only in this fixture.
"""
import argparse,os,tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=9876)
    parser.add_argument('--static-dir',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tingji-c1-ui-') as directory:
        os.environ['MEETING_DATA']=directory
        from unittest.mock import patch
        from fastapi.staticfiles import StaticFiles
        import uvicorn
        from app.main import create_app
        from app.db import uid
        from app.providers import WhisperCpp,QwenMLX
        from scripts.recording_ui_fixture import SyntheticCapture
        with patch('app.main.NativeCapture',SyntheticCapture),patch.object(WhisperCpp,'ready',lambda _:False),patch.object(QwenMLX,'ready',lambda _:False),patch('app.main.LMStudioProvider.status',lambda _:{'ready':False,'models':[],'error':'synthetic fixture','base_url':''}):
            app=create_app(Path(directory),start_worker=False)
            app.state.keep_awake.supported=False
            if args.static_dir:
                next(route for route in app.routes if getattr(route,'path',None)=='').app=StaticFiles(directory=args.static_dir,html=True)
            claims=[];services=app.state.services;store=services.store;rid=uid();jid=uid()
            @app.post('/_fixture/resources/{scenario}')
            def scenario(scenario:str):
                for claim in claims:claim.close()
                claims.clear();services.resources.set_index_deferred([])
                store.execute('DELETE FROM live_sessions');store.execute('DELETE FROM jobs')
                if scenario=='asr':
                    claim=services.live_asr_lock.request('live_asr',{'asr':1});assert claim.acquire(False);claims.append(claim)
                    store.execute("INSERT INTO live_sessions(recording_id,source,config,status) VALUES(?,'browser','{}','running')",(rid,))
                    store.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,'','retrieval_index','queued','等待索引','{}')",(jid,))
                    services.worker.next_job()
                elif scenario=='llm':
                    holder=services.resources.request('live_overview',{'local_llm':1});assert holder.acquire(False);claims.append(holder)
                    waiter=services.live_asr_lock.request('minutes',{'local_llm':1});assert not waiter.acquire(False);claims.append(waiter)
                return {'synthetic':True,'scenario':scenario}
            mount=next(route for route in app.routes if getattr(route,'path',None)=='')
            app.router.routes.remove(mount);app.router.routes.append(mount)
            print('SYNTHETIC C1 RESOURCE FIXTURE ONLY',flush=True)
            try:uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
            finally:
                for claim in claims:claim.close()

if __name__=='__main__':main()
