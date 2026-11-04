"""Isolated streaming/correction UI acceptance. No microphone or real models.
PYTHONPATH=. .venv/bin/python scripts/live_correction_ui_fixture.py
"""
import io,sys,tempfile,wave
from pathlib import Path
import uvicorn
import app.main as module
import app.live_corrections as correction_module
from app.db import Store,uid,encode
from scripts.recording_ui_fixture import SyntheticCapture

class StreamingFixture:
    model='synthetic-qwen';label='合成千问';engine_id='qwen';needs_punctuation=False;chunk_seconds=15
    def identity(self):return []
    def ready(self):return True
    def stream_command(self,audio,output):
        code="import sys,json,time;from pathlib import Path;print(json.dumps({'type':'qwen_partial','text':'请打开'},ensure_ascii=False),flush=True);time.sleep(.15);print(json.dumps({'type':'qwen_partial','text':'请打开听季查看会议记录。'},ensure_ascii=False),flush=True);Path(sys.argv[1]).write_text(json.dumps({'segments':[{'start':0,'end':12,'text':'请打开听季查看会议记录。'}]}))"
        return [sys.executable,'-c',code,str(output)]

def synthetic_window(self,rid,offset,window_seconds=15,responsive=False):
    if offset>=12:return {'ready':False,'next_offset':offset,'skipped_seconds':0}
    duration=min(12,window_seconds);out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\1\0'*16000*duration)
    return {'ready':True,'offset':0,'next_offset':duration,'skipped_seconds':0,'silent':False,'payload':out.getvalue(),'confirmed':window_seconds>=15}

def fake_request(*args,**kwargs):
    return {'choices':[{'finish_reason':'stop','message':{'content':encode({'edits':[{'original':'听季','replacement':'听记','reason':'参考术语为听记；请确认后采用。'}]})}}]}

def main():
    with tempfile.TemporaryDirectory(prefix='tingji-live-correction-ui-') as workspace:
        root=Path(workspace);store=Store(root)
        store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,is_default) VALUES(?,?,?,?,?,1)',(uid(),'合成本地纠错模型','local','http://127.0.0.1:1234/v1','synthetic-correction'))
        module.NativeCapture=SyntheticCapture;SyntheticCapture.draft_window=synthetic_window
        module.get_asr_provider=lambda *_:StreamingFixture()
        module.engine_catalog=lambda:[{'id':'whisper','label':'合成 Whisper','ready':True},{'id':'qwen','label':'合成千问','ready':True}]
        correction_module.request_once=fake_request
        app=module.create_app(root,start_worker=False);app.state.keep_awake.supported=False
        print('SYNTHETIC TEST ONLY — no microphone, no real capture or models.',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=8766,access_log=False)

if __name__=='__main__':main()
