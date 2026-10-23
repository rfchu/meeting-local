"""Isolated UI acceptance fixture: synthetic audio only, no microphone or user database.
Run from the repository root: PYTHONPATH=. .venv/bin/python scripts/recording_ui_fixture.py
"""
import tempfile
from pathlib import Path
from app.main import create_app
import app.main as module
from app.native_capture import NativeCapture
from app.recordings import RecordingStore
from app.db import Store,uid,encode

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
    parser.add_argument('--review-demo',action='store_true',help='Seed a synthetic transcript and AI minutes for review acceptance')
    parser.add_argument('--minutes-demo',action='store_true',help='Seed persistent AI minutes generation failure for UI acceptance')
    parser.add_argument('--recovery-demo',action='store_true',help='Synthetic failed ASR and saved recovery text')
    parser.add_argument('--flow-demo',action='store_true',help='Synthetic meeting preparation, attention and follow-up flow; no real models')
    parser.add_argument('--qa-demo',action='store_true',help='Synthetic multi-meeting QA with deterministic replies, never calls a real model')
    parser.add_argument('--long-demo',type=int,choices=[1000,3000,10000],help='Seed an isolated long transcript for DOM/edit/search acceptance')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tingji-reliability-ui-') as workspace:
        root=Path(workspace)
        store=Store(root);recordings=RecordingStore(store)
        session=recordings.create('audio/webm')
        recordings.metadata(session['id'],[{'captured_at':4,'raw_text':'验证用简记：核对中断后恢复','note_type':'note','note_scope':'personal'}],15,3)
        recordings.chunk(session['id'],0,b'fixture-head');recordings.chunk(session['id'],2,b'fixture-tail')
        if args.review_demo or args.qa_demo or args.flow_demo or args.recovery_demo:
            import wave
            from app.minutes import MeetingMinutes
            from app.notes import insert_note
            mid=uid();directory=store.directory(mid);directory.mkdir()
            with wave.open(str(directory/'original'),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*16000*12)
            store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,'合成验收会议','synthetic.wav',12))
            store.save_revision(mid,[{'id':'demo-seg','start':0,'end':12,'speaker':'发言人 1','text':'测试约定周五交付，负责人仍待确认。','boundary_review':False,'speaker_locked':False}],'manual')
            with store.connect() as db:insert_note(db,mid,{'captured_at':4,'raw_text':'核对交付安排','note_type':'note','note_scope':'personal'})
            document=MeetingMinutes(title='合成验收纪要',style='ai',overview=[{'content':'测试约定周五交付，负责人待确认。','source_segment_ids':['demo-seg']}])
            store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (uid(),mid,1,'meeting_minutes',encode(document.model_dump()),encode({'ai_generated':True,'source_transcript_revision':1})))
        if args.qa_demo:
            from app.qa import Answer
            import app.qa as qa_module
            profile_id=uid()
            store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,is_default) VALUES(?,?,?,?,?,1)',
                (profile_id,'合成回答模型（无真实请求）','local','http://127.0.0.1:1234/v1','synthetic'))
            store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,?,?,?,?)',
                (uid(),'云端确认验收（仅模拟）','cloud','https://example.com/v1','synthetic','fixture-key'))
            second=uid();folder=store.directory(second);folder.mkdir()
            with wave.open(str(folder/'original'),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*16000*12)
            store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(second,'合成排期调整会议','synthetic.wav',12))
            store.save_revision(second,[{'id':'second-seg','start':0,'end':12,'speaker':'发言人 2','text':'项目交付调整到下周一，负责人依然待确认。','boundary_review':False}],'manual')
            def fake_answer(provider,model,question,sources,work,run):
                return Answer(status='answered',claims=[{'text':source['title']+'记载：'+source['content'],
                    'kind':'note' if source['kind'] in ('note','expanded_note') else 'record',
                    'citations':[{'source':source['label'],'quote':source['content']}]} for source in sources[:3]])
            qa_module.generate_answer=fake_answer
        if args.long_demo:
            import wave
            mid=uid();directory=store.directory(mid);directory.mkdir()
            with wave.open(str(directory/'original'),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*16000*12)
            store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,f'合成长文字稿（{args.long_demo} 段）','synthetic-long.wav',args.long_demo*6))
            segments=[{'id':f'long-{i}','start':i*6,'end':i*6+5,'speaker':f'发言人 {i%3+1}',
                'text':f'第 {i+1} 段合成记录：核对项目进度与验收安排。'+('这是较长的合成内容，用于验证动态高度变化。'*(i%5)),
                'boundary_review':False,'speaker_locked':False} for i in range(args.long_demo)]
            segments[-1]['text']='终点标记：星河项目最终交付安排仍需复核。'
            store.save_revision(mid,segments,'manual')
        if args.flow_demo:
            from app.meeting_flow import save_brief
            from app.analysis import AIMinutesResult,AIMinutesBasicInfo,AnalysisItem
            from app.minutes import MinutesItem
            class SyntheticMinutesProvider:
                base_url='http://127.0.0.1:1234/v1'
                def status(self):return {'ready':True,'models':['synthetic-minutes'],'base_url':self.base_url,'error':''}
                def resolve_model(self,model=None):return 'synthetic-minutes'
                def generate_ai_minutes(self,model,segments,metadata,work,run):
                    if args.minutes_demo:
                        import time
                        time.sleep(5) # Allow refreshing a pending synthetic request during acceptance.
                    return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成交付会',source_segment_ids=['demo-seg']),overview=[AnalysisItem(content='本次围绕交付安排进行核对。',source_segment_ids=['demo-seg'])],decisions=[AnalysisItem(content='按周五交付安排推进。',source_segment_ids=['demo-seg'])],open_questions=[AnalysisItem(content='交付负责人仍待确认。',source_segment_ids=['demo-seg'])],core_content=[AnalysisItem(content='测试约定周五交付，负责人待确认。',source_segment_ids=['demo-seg'])],action_items=[AnalysisItem(content='核对交付负责人',source_segment_ids=['demo-seg'])],summary=[AnalysisItem(content='继续确认交付安排。',source_segment_ids=['demo-seg'])])
            module.LMStudioProvider=SyntheticMinutesProvider
            earlier=uid();folder=store.directory(earlier);folder.mkdir();(folder/'original').write_bytes((directory/'original').read_bytes())
            store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(earlier,'合成上次评审会','previous.wav',12))
            store.save_revision(earlier,[{'id':'previous-seg','start':0,'end':12,'speaker':'甲','text':'准备交付验收材料。'}],'manual')
            with store.connect() as db:
                old=insert_note(db,earlier,{'captured_at':3,'raw_text':'准备验收材料','note_type':'action','note_scope':'personal'})
                from app.notes import fingerprint
                n=db.execute('SELECT * FROM meeting_notes WHERE id=?',(old,)).fetchone()
                db.execute('INSERT INTO note_todos(id,meeting_id,note_id,content,revision,note_fingerprint,owner) VALUES(?,?,?,?,?,?,?)',(uid(),earlier,old,'准备验收材料',1,fingerprint(n),'测试负责人'))
                save_brief(db,mid,{'title':'合成验收会议','purpose':'核对交付安排','questions':'负责人是否明确？','previous_meeting_id':earlier})
                insert_note(db,mid,{'captured_at':5,'raw_text':'我的私人担忧，不能直接写进纪要','note_type':'note','note_scope':'personal'})
                insert_note(db,mid,{'captured_at':7,'raw_text':'负责人到底是谁？','note_type':'question','note_scope':'personal'})
            document.action_items=[MinutesItem(content='核对交付负责人',source_segment_ids=['demo-seg'])]
            store.execute("UPDATE artifacts SET payload=?,provenance=? WHERE meeting_id=? AND kind='meeting_minutes'",(encode(document.model_dump()),encode({'edited':True,'ai_generated':True}),mid))
        if args.recovery_demo:
            jid=uid()
            store.execute('INSERT INTO jobs(id,meeting_id,state,stage,error,config) VALUES(?,?,?,?,?,?)',(jid,mid,'failed','处理失败','合成异常：补识别片段未完成',encode({'engine':'qwen','completion_mode':'full','pipeline_version':2})))
            recovered=[{'id':'recovery-seg','start':0,'end':8,'text':'这是一段已保留的合成会议文字，对齐异常并不会使这段文字消失。','speaker':'未分配','timestamp_precision':'chunk','alignment_warning':'对齐失败，仅保留音频块范围；请回听校准'}]
            store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(uid(),mid,1,'transcript_candidate',encode(recovered),encode({'incomplete':True,'job':jid,'missing_ranges':[{'start':8,'end':12}]})))
            store.save_revision(mid,recovered,'synthetic-recovery')
        if args.minutes_demo:
            import time
            (directory/'minutes-generation.json').write_text(encode({'id':'synthetic-generation','state':'failed','revision':1,'started_at':time.time()-90,'finished_at':time.time(),'error':'合成验收：模型输出被截断，未保存为完整纪要。'}))
        previous=module.NativeCapture
        try:
            module.NativeCapture=SyntheticCapture
            app=create_app(root,start_worker=bool(args.qa_demo or args.long_demo))
        finally:module.NativeCapture=previous
        if args.qa_demo or args.long_demo:
            actual_process=app.state.worker.process
            def only_qa(job):
                if job.get('kind') not in ('retrieval_answer','retrieval_index'):raise ValueError('隔离验收服务仅处理合成问答和索引')
                return actual_process(job)
            app.state.worker.process=only_qa
        app.state.keep_awake.supported=False
        print('SYNTHETIC TEST ONLY — no microphone, no real capture; temporary data removed on exit.',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
