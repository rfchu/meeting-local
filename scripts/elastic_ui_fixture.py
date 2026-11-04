"""Synthetic 25s hard boundary + 5s continuation; no microphone or real models.
PYTHONPATH=. .venv/bin/python scripts/elastic_ui_fixture.py
"""
import sys,tempfile,wave
from pathlib import Path
import uvicorn
import app.main as module
from app.native_capture import NativeCapture
from app.db import Store,uid
from scripts.recording_ui_fixture import SyntheticCapture

RAW=b'\x70\x17'*25*16000+b'\x58\x1b'*round(4.2*16000)+b'\0\0'*round(.8*16000)
class ElasticCapture(SyntheticCapture):
    def start(self,microphone=True):
        session=super().start(microphone)
        (self.recordings.folder(session['id'])/'system.pcm').write_bytes(RAW)
        self.write_status(session['id']);return session
    def write_status(self,rid):
        with self.lock:
            status={'state':self.states.get(rid,'interrupted'),'duration':30,'error':'','system_frames':480000,
                    'microphone_frames':0,'silence_seconds':{'system':.8},'system_levels':[.1,.2,.12,.04]*18,'microphone_levels':[]}
            self.recordings.write(self.recordings.folder(rid)/'capture.json',status);return status
    def status(self,rid,touch=True):
        result=super().status(rid,touch);result['health']['tracks'][0]['saved_seconds']=30;return result
    draft_window=NativeCapture.draft_window

class ASR:
    model='synthetic-elastic';label='合成千问';engine_id='qwen';needs_punctuation=False;chunk_seconds=15
    def ready(self):return True
    def identity(self):return []
    def stream_command(self,audio,output):
        code="""
import json,sys,wave,time,struct
from pathlib import Path
with wave.open(sys.argv[2],'rb') as wav:
 duration=wav.getnframes()/16000;sample=struct.unpack('<h',wav.readframes(1))[0]
text='这个功能我们计划在下周' if sample==6000 else '完成验证，然后再发布。'
print(json.dumps({'type':'qwen_partial','text':text},ensure_ascii=False),flush=True)
time.sleep(.2)
Path(sys.argv[1]).write_text(json.dumps({'segments':[{'start':0,'end':duration,'text':text}]}))
"""
        return [sys.executable,'-c',code,str(output),str(audio)]

def main():
    with tempfile.TemporaryDirectory(prefix='tingji-elastic-ui-') as workspace:
        store=Store(workspace);mid=uid();directory=store.directory(mid);directory.mkdir()
        with wave.open(str(directory/'original'),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(RAW)
        store.execute("INSERT INTO meetings(id,title,filename,duration) VALUES(?,'合成连续发言阅读','synthetic.wav',30)",(mid,))
        common={'speaker':'未分配','boundary_review':False,'continuation_id':mid+':0'}
        store.save_revision(mid,[dict(common,id='elastic-a',start=0,end=25,text='这个功能我们计划在下周',continues_previous=False,continues_next=True,boundary_reason='hard_limit'),
            dict(common,id='elastic-b',start=25,end=30,text='完成验证，然后再发布。',continues_previous=True,continues_next=False,boundary_reason='silence')],'synthetic')
        store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,is_default) VALUES(?,?,?,?,?,1)',(uid(),'合成配置（不调用模型）','local','http://127.0.0.1:1234/v1','synthetic'))
        module.NativeCapture=ElasticCapture;module.get_asr_provider=lambda *_:ASR()
        module.engine_catalog=lambda:[{'id':'whisper','label':'合成 Whisper','ready':True},{'id':'qwen','label':'合成千问','ready':True}]
        app=module.create_app(workspace,start_worker=False);app.state.keep_awake.supported=False
        print('SYNTHETIC ONLY — no microphone, no real models.',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=8766,access_log=False)

if __name__=='__main__':main()
