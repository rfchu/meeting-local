"""F2 private quality UI fixture: synthetic observation only, no devices, models or real acceptance."""
import argparse,json,tempfile,time,uuid
from pathlib import Path
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--static-dir',type=Path,required=True);parser.add_argument('--port',type=int,default=9876);args=parser.parse_args()
    from app.main import create_app
    from app.providers import WhisperCpp,QwenMLX
    import uvicorn
    with tempfile.TemporaryDirectory(prefix='tjf2-ui-',dir='/private/tmp') as temporary:
        with patch.object(WhisperCpp,'ready',lambda _:False),patch.object(QwenMLX,'ready',lambda _:False):
            app=create_app(Path(temporary),start_worker=False);checks=app.state.recording_validation
            cid=uuid.uuid4().hex;rid=uuid.uuid4().hex;started=time.time()-65
            samples=[{'phase':'recording','capture':'recording','storage':'writing','asr':'disabled','saved_bytes':i*160000+32000,'saved_seconds':i*5+1,'covered_seconds':0,'file_growth_bytes':160000 if i else None,'flags':[]} for i in range(13)]
            summary={'sample_count':13,'flags':{},'end_reason':'target_elapsed','last_sample':samples[-1],'max_observation_gap_seconds':5,'capture_environment':app.state.recording_acceptance.environment()}
            with checks.connect() as db:
                db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?)',(cid,rid,'completed',60,started,started+60,60,json.dumps(summary)))
                db.executemany('INSERT INTO samples VALUES(?,?,?,?,?)',[(cid,i+1,started+i*5,i*5,json.dumps(s)) for i,s in enumerate(samples)])
            import io,wave
            payload=io.BytesIO()
            with wave.open(payload,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x70\x17'*32000)
            app.state.quality_samples.add(payload.getvalue(),['terms'],True,True)
            static=next(route for route in app.routes if getattr(route,'path',None)=='').app
            static.directory=str(args.static_dir);static.all_directories=[str(args.static_dir)]
            print('F2 SYNTHETIC UI ONLY: no real sample annotation or inference',flush=True)
            uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
