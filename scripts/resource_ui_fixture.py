"""C1/C2/C3/C4/D1/D2/D3 synthetic resource UI: PYTHONPATH=. .venv/bin/python scripts/resource_ui_fixture.py.
No models, microphone or user data. Scenario controls exist only in this fixture.
"""
import argparse,os,tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=9876)
    parser.add_argument('--static-dir',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tingji-d3-ui-') as directory:
        os.environ['MEETING_DATA']=directory
        from unittest.mock import patch
        from fastapi.staticfiles import StaticFiles
        import uvicorn
        from app.main import create_app
        from app.db import uid
        from app.providers import WhisperCpp,QwenMLX
        from app.analysis import AIMinutesResult,AIMinutesBasicInfo,AnalysisItem,LMStudioProvider
        def fake_minutes(*args,**kwargs):
            return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成纪要交接验收',source_segment_ids=['s1']),core_content=[AnalysisItem(content='周五由甲提交合成材料。',source_segment_ids=['s1'])],action_items=[AnalysisItem(content='提交合成材料',owner='甲',due_date='周五',source_segment_ids=['s1'])])
        from scripts.recording_ui_fixture import SyntheticCapture
        with patch.object(LMStudioProvider,'generate_ai_minutes',fake_minutes),patch.object(LMStudioProvider,'resolve_model',lambda self,model=None:model or 'synthetic'),patch('app.main.NativeCapture',SyntheticCapture),patch.object(WhisperCpp,'ready',lambda _:False),patch.object(QwenMLX,'ready',lambda _:False),patch('app.main.LMStudioProvider.status',lambda _:{'ready':False,'models':[],'error':'synthetic fixture','base_url':''}):
            app=create_app(Path(directory),start_worker=False)
            app.state.keep_awake.supported=False
            if args.static_dir:
                next(route for route in app.routes if getattr(route,'path',None)=='').app=StaticFiles(directory=args.static_dir,html=True)
            claims=[];services=app.state.services;store=services.store;rid=uid();jid=uid();mid=uid();pid=uid()
            @app.post('/_fixture/resources/{scenario}')
            def scenario(scenario:str):
                if scenario=='delivery-ready':
                    from app.minutes import MeetingMinutes,MinutesItem
                    from app import minutes_versions
                    with store.connect() as db:
                        if not minutes_versions.selected(db,mid):
                            minutes_versions.publish_draft(db,mid,1,MeetingMinutes(title='合成交付闭环验收',style='ai',overview=[MinutesItem(content='周五提交材料',source_segment_ids=['s1'])],action_items=[MinutesItem(content='提交材料',source_segment_ids=['s1'],owner='甲',due_date='周五')]),{})
                    for step in ('transcript','speakers'):
                        value=services.meeting_review.read(mid)['steps'][step]
                        services.meeting_review.set(mid,step,value['fingerprint'],'confirmed')
                    return {'synthetic':True,'delivery_ready':True}
                if scenario=='delivery-task-change':
                    from app import minutes_versions,meeting_flow
                    from app.http_schemas import ActionTrackInput
                    with store.connect() as db:artifact=minutes_versions.selected(db,mid)
                    result=meeting_flow.track_action(store,mid,artifact['id'],0,ActionTrackInput(content='提交材料',owner='甲',due_date='周五',confirmed=True))
                    store.execute("UPDATE note_todos SET owner='乙',due_date='下周',status='done' WHERE id=?",(result['id'],))
                    return {'synthetic':True,'task_changed':True}
                if scenario=='source-changed':
                    from app.minutes import MeetingMinutes,MinutesItem
                    from app import minutes_versions
                    with store.connect() as db:
                        if not minutes_versions.selected(db,mid):
                            minutes_versions.publish_draft(db,mid,1,MeetingMinutes(title='合成纪要来源核对',style='ai',overview=[MinutesItem(content='周五提交材料',source_segment_ids=['s1'])]),{})
                    store.save_revision(mid,[{'id':'s1','start':0,'end':2,'speaker':'甲','text':'更正：周四由甲提交合成材料。'}],'manual')
                    return {'synthetic':True,'source_changed':True}
                if scenario=='handoff-interrupted':
                    from app.db import encode
                    job_id=uid()
                    store.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config,error) VALUES(?,?,'minutes','failed','处理失败',?,'synthetic JSON format')",(job_id,mid,encode({'revision':1,'model':'synthetic'})))
                    for step in ('transcript','speakers'):
                        value=services.meeting_review.read(mid)['steps'][step]
                        services.meeting_review.set(mid,step,value['fingerprint'],'confirmed')
                    return {'synthetic':True,'interrupted':True}
                if scenario=='handoff-ready':
                    for step in ('transcript','speakers','minutes'):
                        value=services.meeting_review.read(mid)['steps'][step]
                        services.meeting_review.set(mid,step,value['fingerprint'],'confirmed')
                    return {'synthetic':True,'reviewed':True}
                if scenario=='finish-editorial':
                    job=store.one("SELECT * FROM jobs WHERE kind='minutes' AND state='queued' LIMIT 1")
                    if not job:return {'synthetic':True,'completed':False}
                    store.execute("UPDATE jobs SET state='running' WHERE id=?",(job['id'],));services.worker.process(job)
                    return {'synthetic':True,'completed':True}
                for claim in claims:claim.close()
                claims.clear();services.resources.set_index_deferred([]);services.resources.set_realtime([])
                store.execute('DELETE FROM live_sessions');store.execute('DELETE FROM jobs')
                if scenario=='asr':
                    claim=services.live_asr_lock.request('live_asr',{'asr':1});assert claim.acquire(False);claims.append(claim)
                    store.execute("INSERT INTO live_sessions(recording_id,source,config,status) VALUES(?,'browser','{}','running')",(rid,))
                    store.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,'','retrieval_index','queued','等待索引','{}')",(jid,))
                    services.worker.next_job()
                elif scenario=='priority':
                    store.execute("INSERT INTO live_sessions(recording_id,source,config,status) VALUES(?,'browser','{}','running')",(rid,))
                    store.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,'','analysis','running','会中识别优先，等待继续','{}')",(jid,))
                    services.live_sessions.sync_resources()
                    waiter=services.live_asr_lock.request('analysis',{'local_llm':1},jid);assert not waiter.acquire(False);claims.append(waiter)
                elif scenario=='editorial':
                    if not store.one('SELECT id FROM meetings WHERE id=?',(mid,)):
                        store.directory(mid).mkdir()
                        import wave
                        with wave.open(str(store.directory(mid)/'original'),'wb') as audio:
                            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*32000)
                        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成纪要交接验收','synthetic.wav'))
                        store.save_revision(mid,[{'id':'s1','start':0,'end':2,'speaker':'甲','text':'周五由甲提交合成材料。'}],'manual')
                        store.execute("INSERT INTO meeting_notes(id,meeting_id,raw_text,note_scope,captured_at) VALUES(?,?,?,'personal',1)",(uid(),mid,'仅供个人回顾'))
                        store.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,'local',?,?)",(pid,'合成模型（无推理）','http://127.0.0.1:1234/v1','synthetic'))
                elif scenario=='recovery':
                    from app.job_checkpoints import JobCheckpoints
                    store.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,'','analysis','queued','等待恢复，检查点将重新校验','{}')",(jid,))
                    checkpoint=JobCheckpoints(store,store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
                    checkpoint.save('map-0000',{'summary':[]},['synthetic input'])
                elif scenario=='llm':
                    holder=services.resources.request('live_overview',{'local_llm':1});assert holder.acquire(False);claims.append(holder)
                    waiter=services.live_asr_lock.request('minutes',{'local_llm':1});assert not waiter.acquire(False);claims.append(waiter)
                return {'synthetic':True,'scenario':scenario}
            mount=next(route for route in app.routes if getattr(route,'path',None)=='')
            app.router.routes.remove(mount);app.router.routes.append(mount)
            print('SYNTHETIC RESOURCE/RECOVERY FIXTURE ONLY',flush=True)
            try:uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
            finally:
                for claim in claims:claim.close()

if __name__=='__main__':main()
