"""Isolated live-overview acceptance, synthetic capture/ASR/LLM only.
PYTHONPATH=. .venv/bin/python scripts/overview_ui_fixture.py --port 9876
"""
import argparse,tempfile
from pathlib import Path
from app.main import create_app
import app.main as module
import app.onboarding as onboarding
import app.live_overview as overview
from app.native_capture import NativeCapture
from scripts.recording_ui_fixture import SyntheticCapture
from app.db import uid

TEXTS=['首版支持录音、文字校对和纪要导出，实时翻译留待后续讨论。',
'陈明负责周五前交付导出样式预览稿。上线日期还未确定。',
'王宁整理测试清单，下次评审再确认上线日期。']
class Capture(SyntheticCapture):
    def start(self,microphone=True):
        session=super().start(microphone)
        (self.recordings.folder(session['id'])/'system.pcm').write_bytes(b'\x02\x00'*16000*45)
        return session
    def write_status(self,rid):
        with self.lock:
            status={'state':self.states.get(rid,'interrupted'),'duration':45,'error':'','system_frames':720000,
                'system_levels':[.02,.06,.1,.04]*18,'microphone_levels':[]}
            self.recordings.write(self.recordings.folder(rid)/'capture.json',status);return status
    draft_window=NativeCapture.draft_window
class ASR:
    label='合成 Whisper';needs_punctuation=False
    def __init__(self):self.calls=0
    def ready(self):return True
    def identity(self):return {'synthetic':True}
    def transcribe(self,audio,output,run):
        text="".join(TEXTS);self.calls+=1
        return [{'start':0,'end':15,'text':text}]
    transcribe_live=transcribe
    def live_command(self,audio,output):
        import sys,json
        segments=[{'start':0,'end':15,'text':"".join(TEXTS)}]
        return [sys.executable,'-c','import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2])',str(output.with_suffix('.json')),json.dumps(segments)]
    def read_live_result(self,output):
        import json
        return json.loads(output.with_suffix('.json').read_text())

def generate(plan):
    sources=plan['input']['context']+plan['input']['new_segments'];old={i['kind']:i['id'] for i in plan['input']['current_items']}
    items=[]
    for kind,needle,content in [('topic','首版支持录音','明确首版范围与交付安排'),('point','实时翻译留待后续讨论','实时翻译暂不进入首版范围。'),
        ('action','陈明负责周五前交付导出样式预览稿','陈明负责周五前提供导出样式预览稿。'),('question','上线日期还未确定','上线日期仍待测试与后续评审确认。')]:
        source=next((s for s in sources if needle in s['text']),None)
        if source:items.append({'id':old.get(kind),'kind':kind,'content':content,'evidence':[{'segment_id':source['id'],'quote':needle}],
            'note_ids':[n['id'] for n in plan['input']['focus_notes']][:1] if kind=='action' else []})
    return {'items':items}

def main():
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=9876);parser.add_argument('--failure-demo',action='store_true');parser.add_argument('--tasks-demo',action='store_true');args=parser.parse_args()
    module.NativeCapture=Capture;asr=ASR();module.get_asr_provider=lambda _:asr
    catalog=lambda:[{'id':'whisper','label':'Whisper（合成测试）','ready':True},{'id':'qwen','label':'Qwen（测试不启用）','ready':False}]
    module.engine_catalog=catalog;onboarding.engine_catalog=catalog;overview.INTERVAL=5
    with tempfile.TemporaryDirectory(prefix='tingji-overview-ui-') as root:
        app=create_app(root,start_worker=False)
        pid=uid()
        app.state.store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,is_default) VALUES(?,?,?,?,?,1)',
            (pid,'本地概览（合成测试）','local','http://127.0.0.1:1234/v1','synthetic-overview'))
        if args.tasks_demo:
            import json
            app.state.store.execute("INSERT INTO meetings(id,title,filename,duration) VALUES('task-meeting','合成任务验收会议','synthetic.wav',45)")
            config=json.dumps({'profile_id':pid,'model':'synthetic-overview','revision':1})
            app.state.store.execute("INSERT INTO jobs(id,meeting_id,state,kind,stage,progress,config) VALUES('task-running','task-meeting','running','analysis','等待语音识别资源',.25,?)",(config,))
            app.state.store.execute("INSERT INTO jobs(id,meeting_id,state,kind,stage,progress,error,config) VALUES('task-failed','task-meeting','failed','minutes','处理失败',.5,'JSONDecodeError synthetic-private-log',?)",(config,))
        attempts=0
        def reply(plan):
            nonlocal attempts
            attempts+=1
            if args.failure_demo and attempts==1:raise ValueError('合成验收：模型生成超时，原文已保留')
            return generate(plan)
        app.state.live_overview.generator=reply
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',timeout_graceful_shutdown=3)
if __name__=='__main__':main()
