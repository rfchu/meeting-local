"""Meetings HTTP routes; shared services are supplied by the composition root."""
from ..resources import optional_slot
from ..task_management import retry_blocker, classify
from ..model_preferences import resolve_profile
import hashlib, json, shutil, subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from fastapi import File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, Response
from ..db import uid, encode
from ..providers import LocalDiarization
from ..analysis import OpenAICompatibleProvider, analysis_chunk_chars, SECTION_NAMES
from ..transcript_paragraphs import paragraphs
from ..qa import QA
from .. import meeting_flow, minutes_delivery
from ..http_schemas import ActionTrackInput, AnalysisJobInput, AttentionDecisionInput, DeliveryInput, JobInput, MeetingBriefInput, MeetingNoteInput, MeetingNoteUpdate, MeetingNotesBatchInput, NoteAcceptInput, NoteUseInput, RenameInput, ReviewCheckpointInput, RevisionInput, SpeakerReviewApplyInput, TodoUpdate
from ..application_services import ApplicationServices

def register_routes(app,services:ApplicationServices):
    decorated=services.meetings.decorated
    live_results=services.live_results
    meeting=services.meetings.meeting
    meeting_review=services.meeting_review
    public_artifact=services.meetings.public_artifact
    recordings=services.recordings
    store=services.store
    worker=services.worker
    get_asr_provider=services.providers.asr
    LMStudioProvider=services.providers.llm
    generate_review=services.providers.speaker_review
    prepare_auto_minutes=services.providers.auto_minutes

    @app.put('/api/meetings/{mid}/review/{step}')
    def confirm_review(mid:str,step:Literal['transcript','speakers','notes','minutes'],body:ReviewCheckpointInput):
        try:return meeting_review.set(mid,step,body.fingerprint,body.decision)
        except KeyError:raise HTTPException(404,'会议不存在')
        except ValueError as error:raise HTTPException(409,str(error))


    @app.delete('/api/meetings/{mid}/review/{step}')
    def reset_review(mid:str,step:Literal['transcript','speakers','notes','minutes']):
        try:return meeting_review.reset(mid,step)
        except KeyError:raise HTTPException(404,'会议不存在')


    @app.get('/api/meeting-preparation')
    def get_preparation():
        row=store.one('SELECT payload FROM meeting_preparation WHERE id=1')
        return json.loads(row['payload']) if row else dict(meeting_flow.EMPTY_BRIEF)


    @app.put('/api/meeting-preparation')
    def set_preparation(body:MeetingBriefInput):
        if body.previous_meeting_id and not store.one('SELECT id FROM meetings WHERE id=?',(body.previous_meeting_id,)):raise HTTPException(409,'关联会议不存在')
        store.execute('INSERT OR REPLACE INTO meeting_preparation(id,payload) VALUES(1,?)',(encode(body.model_dump()),))
        return body.model_dump()


    @app.get('/api/meetings')
    def list_meetings():
        rows=store.all('SELECT id,title,filename,created,duration,revision FROM meetings ORDER BY created DESC,rowid DESC')
        for row in rows: row['job']=store.one('SELECT * FROM jobs WHERE meeting_id=? ORDER BY created DESC,rowid DESC LIMIT 1',(row['id'],))
        return rows


    @app.post('/api/meetings',status_code=201)
    async def upload(file:UploadFile=File(...)):
        filename=Path(file.filename or '录音.wav').name
        if Path(filename).suffix.lower() not in {'.wav','.mp3','.m4a','.mp4','.mov','.aac','.flac','.ogg','.webm'}:
            raise HTTPException(400,'不支持此格式，请导入音频或视频文件')
        mid=uid(); folder=store.directory(mid); folder.mkdir()
        size=0; digest=hashlib.sha256()
        try:
            with (folder/'original').open('wb') as f:
                while block:=await file.read(1024*1024):
                    size+=len(block)
                    if size>4*1024**3: raise HTTPException(413,'文件最大支持 4GB')
                    f.write(block); digest.update(block)
            if not size: raise HTTPException(400,'文件为空')
            (folder/'asset.json').write_text(encode({'filename':filename,'size':size,'sha256':digest.hexdigest()}))
            store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,Path(filename).stem,filename))
        except BaseException:
            shutil.rmtree(folder,ignore_errors=True); raise
        finally: await file.close()
        return decorated(meeting(mid))


    @app.get('/api/meetings/{mid}')
    def detail(mid:str): return decorated(meeting(mid))


    @app.patch('/api/meetings/{mid}')
    def rename(mid:str,body:RenameInput):
        meeting(mid); store.execute('UPDATE meetings SET title=? WHERE id=?',(body.title,mid)); return {'ok':True}


    @app.put('/api/meetings/{mid}/brief')
    def update_brief(mid:str,body:MeetingBriefInput):
        meeting(mid)
        try:
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE');meeting_flow.save_brief(db,mid,body.model_dump())
                if body.title.strip():db.execute('UPDATE meetings SET title=? WHERE id=?',(body.title.strip(),mid))
            return meeting_flow.flow(store,mid)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.put('/api/meetings/{mid}/notes/{nid}/disposition')
    def attention_disposition(mid:str,nid:str,body:AttentionDecisionInput):
        try:meeting_flow.review_attention(store,mid,nid,body);return meeting_flow.flow(store,mid)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.get('/api/followups')
    def followups():
        values=[]
        for row in store.all("SELECT id,title FROM meetings WHERE EXISTS(SELECT 1 FROM note_todos WHERE meeting_id=meetings.id) ORDER BY created DESC,rowid DESC"):
            value=decorated(meeting(row['id']))
            for todo in value['note_todos']:
                if todo.get('carried'):continue
                values.append({**todo,'meeting_id':row['id'],'meeting_title':row['title']})
        return values


    @app.post('/api/meetings/{mid}/speaker-reviews',status_code=201)
    def create_speaker_review(mid:str,body:AnalysisJobInput):
        row=meeting(mid);segments=json.loads(row['segments'])
        if not segments:raise HTTPException(409,'请先完成文字稿')
        speakers={s['speaker'] for s in segments if s.get('speaker') and s['speaker']!='未分配'}
        if len(speakers)<2:raise HTTPException(409,'请先完成声学发言人区分或手动标注至少两位发言人')
        profile=resolve_profile(store,'speakers',body.profile_id)
        try:
            provider=OpenAICompatibleProvider(profile) if profile else LMStudioProvider()
            model=provider.resolve_model(body.model);aid=uid()
            max_chars=analysis_chunk_chars(profile['context_window'],profile['analysis_chunk_mode'],profile['analysis_chunk_chars']) if profile else 8000
            resource='cloud_llm' if profile and profile['provider_type']=='cloud' else 'local_llm'
            with optional_slot(services.resources,'speaker_review',resource,('speaker_review',mid),legacy=resource=='local_llm'):
                payload=generate_review(provider,model,segments,store.directory(mid)/('speaker-review-'+aid),max_chars)
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:raise HTTPException(409,str(error))
        provenance={'model':model,'provider':profile['name'] if profile else 'LM Studio','provider_type':profile['provider_type'] if profile else 'local','version':'speaker-review-v1'}
        store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
            (aid,mid,row['revision'],'speaker_review',encode(payload),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))


    @app.post('/api/meetings/{mid}/speaker-reviews/{aid}/apply')
    def apply_speaker_review(mid:str,aid:str,body:SpeakerReviewApplyInput):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
            if not row:raise HTTPException(404,'会议不存在')
            artifact=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='speaker_review'",(aid,mid)).fetchone()
            if not artifact:raise HTTPException(404,'校对建议不存在')
            if row['revision']!=body.revision or artifact['revision']!=body.revision:raise HTTPException(409,'文字稿已变更，请重新生成校对建议')
            suggestions={s['segment_id']:s for s in json.loads(artifact['payload'])['suggestions']}
            selected=set(body.segment_ids)
            if not selected.issubset(suggestions):raise HTTPException(400,'包含不存在的校对建议')
            segments=json.loads(row['segments']);found=set()
            for segment in segments:
                if segment['id'] in selected:
                    suggestion=suggestions[segment['id']]
                    if segment.get('speaker_locked') or segment['speaker']!=suggestion['original_speaker']:raise HTTPException(409,'发言人已确认或修改，请重新生成建议')
                    segment['speaker']=suggestion['proposed_speaker'];segment['speaker_locked']=True;found.add(segment['id'])
            if found!=selected:raise HTTPException(409,'原文片段已变更')
            revision=body.revision+1;payload=encode(segments)
            source=encode({'kind':'speaker_review','review_id':aid,'accepted_segment_ids':sorted(selected)})
            db.execute('INSERT INTO revisions(meeting_id,revision,segments,source) VALUES(?,?,?,?)',(mid,revision,payload,source))
            db.execute('UPDATE meetings SET revision=?,segments=? WHERE id=?',(revision,payload,mid))
        return {'revision':revision}


    @app.post('/api/meetings/{mid}/notes',status_code=201)
    def create_note(mid:str,body:MeetingNoteInput):
        from ..notes import insert_note
        meeting(mid)
        with store.connect() as db:nid=insert_note(db,mid,body.model_dump())
        note=store.one('SELECT * FROM meeting_notes WHERE id=?',(nid,))
        note['source_segment_ids']=json.loads(note['source_segment_ids'] or '[]')
        return note


    @app.post('/api/meetings/{mid}/notes/batch',status_code=201)
    def create_notes_batch(mid:str,body:MeetingNotesBatchInput):
        meeting(mid);created=[]
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            from ..notes import insert_note
            for item in body.notes:created.append(insert_note(db,mid,item.model_dump()))
        return {'ids':created,'count':len(created)}


    @app.put('/api/meetings/{mid}/notes/{nid}')
    def update_note(mid:str,nid:str,body:MeetingNoteUpdate):
        meeting(mid)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)).fetchone()
            if not old:raise HTTPException(404,'简记不存在')
            text=body.raw_text.strip();scope=body.note_scope or old['note_scope']
            marker=bool(old['is_marker']) if body.is_marker is None else body.is_marker
            position=old['captured_at'] if body.captured_at is None else body.captured_at
            if not text and not marker:raise HTTPException(422,'请补充内容或保留时间标记')
            if any([text!=old['raw_text'],body.note_type!=old['note_type'],scope!=old['note_scope'],marker!=bool(old['is_marker']),position!=old['captured_at']]):
                db.execute("UPDATE meeting_notes SET raw_text=?,note_type=?,note_scope=?,is_marker=?,captured_at=?,expanded_text='',accepted_text='',source_segment_ids='[]',status='raw',expansion_revision=0,updated=CURRENT_TIMESTAMP WHERE id=?",(text,body.note_type,scope,int(marker),position,nid))
        return {'ok':True}


    @app.get('/api/meetings/{mid}/notes/{nid}/review')
    def review_note(mid:str,nid:str):
        from ..notes import review
        try:return review(store,mid,nid)
        except ValueError as error:raise HTTPException(404,str(error))


    @app.post('/api/meetings/{mid}/notes/{nid}/use')
    def use_meeting_note(mid:str,nid:str,body:NoteUseInput):
        from ..notes import use_note
        try:return use_note(store,mid,nid,body)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.post('/api/meetings/{mid}/minutes/{aid}/actions/{index}/track',status_code=201)
    def track_minutes_action(mid:str,aid:str,index:int,body:ActionTrackInput):
        try:return meeting_flow.track_action(store,mid,aid,index,body)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.put('/api/meetings/{mid}/note-todos/{tid}')
    def update_note_todo(mid:str,tid:str,body:TodoUpdate):
        meeting(mid)
        if not store.one('SELECT id FROM note_todos WHERE id=? AND (meeting_id=? OR EXISTS(SELECT 1 FROM meeting_task_links WHERE meeting_id=? AND task_id=note_todos.id))',(tid,mid,mid)):raise HTTPException(404,'待办不存在')
        store.execute('UPDATE note_todos SET status=?,owner=?,due_date=?,updated=CURRENT_TIMESTAMP WHERE id=?',(body.status,body.owner.strip(),body.due_date.strip(),tid))
        return {'ok':True}


    @app.post('/api/meetings/{mid}/tasks/{tid}/carry',status_code=201)
    def carry_task(mid:str,tid:str):
        try:return meeting_flow.carry_task(store,mid,tid)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.delete('/api/meetings/{mid}/tasks/{tid}/link')
    def unlink_task(mid:str,tid:str):
        meeting(mid)
        store.execute('DELETE FROM meeting_task_links WHERE meeting_id=? AND task_id=?',(mid,tid))
        return {'ok':True}


    @app.post('/api/meetings/{mid}/minutes/deliveries',status_code=201)
    def create_delivery(mid:str,body:DeliveryInput):
        try:return minutes_delivery.create(store,mid,body.artifact_id,body.include_citations,body.confirmed)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.post('/api/meetings/{mid}/notes/{nid}/expand',status_code=201)
    def expand_note(mid:str,nid:str,body:AnalysisJobInput):
        from ..note_expansion import nearby_segments
        row=meeting(mid)
        note=store.one('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid))
        if not note:raise HTTPException(404,'简记不存在')
        if not note['raw_text'].strip():raise HTTPException(409,'时间标记需要先补充内容，再请求扩写')
        if not row['revision']:raise HTTPException(409,'请先完成文字稿，再关联依据扩写')
        profile=resolve_profile(store,'notes',body.profile_id)
        try:
            if profile:
                provider=OpenAICompatibleProvider(profile);model=provider.resolve_model(body.model)
                provider_snapshot={'id':profile['id'],'name':profile['name'],'provider_type':profile['provider_type'],'base_url':profile['base_url'],
                    'context_window':profile['context_window'],'analysis_chunk_mode':profile['analysis_chunk_mode']}
            else:
                provider=LMStudioProvider();model=provider.resolve_model(body.model);provider_snapshot={'name':'LM Studio','provider_type':'local','base_url':getattr(provider,'base_url','http://127.0.0.1:1234/v1')}
        except (RuntimeError,ValueError) as error:raise HTTPException(409,str(error))
        jid=uid()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
            note=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)).fetchone()
            if not note:raise HTTPException(404,'简记不存在')
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone():raise HTTPException(409,'已有任务正在处理')
            budget=min(8000,int(((int(profile['context_window']) if profile else 32768)-2048-1200)*.6)-len(note['raw_text']))
            if budget<256:raise HTTPException(409,'当前模型上下文不足以容纳简记和依据，请缩短简记或选择更大上下文模型')
            config={'note_id':nid,'revision':current['revision'],'profile_id':profile['id'] if profile else None,'provider':provider_snapshot,'model':model,
                **{key:note[key] for key in ('raw_text','note_type','captured_at','note_scope')},'segments':nearby_segments(json.loads(current['segments']),note['captured_at'],budget)}
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)',(jid,mid,'queued','等待简记扩写',encode(config),'note_expansion'))
        return {'id':jid,'model':model,'revision':current['revision']}


    @app.post('/api/meetings/{mid}/notes/{nid}/accept-expansion')
    def accept_expansion(mid:str,nid:str,body:NoteAcceptInput):
        from ..note_expansion import Expansion,validate_expansion
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM meetings WHERE id=?',(mid,)).fetchone()
            note=db.execute('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)).fetchone()
            artifact=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='note_expansion'",(body.artifact_id,mid)).fetchone()
            if not row or not note or not artifact:raise HTTPException(404,'简记或扩写建议不存在')
            payload=json.loads(artifact['payload'])
            if payload.get('note_id')!=nid:raise HTTPException(400,'扩写建议不属于这条简记')
            if artifact['revision']!=row['revision'] or payload.get('note_scope','personal')!=note['note_scope'] or any(payload.get(key)!=note[key] for key in ('raw_text','note_type','captured_at')):raise HTTPException(409,'文字稿或简记已变化，请重新扩写')
            if payload['status']!='supported':raise HTTPException(409,'依据不足，无法接受扩写')
            try:result=validate_expansion(Expansion.model_validate(payload),json.loads(row['segments']))
            except ValueError as error:raise HTTPException(409,str(error))
            accepted=result.text if body.accepted_text is None else body.accepted_text.strip()
            if not accepted:raise HTTPException(422,'接受的扩写内容不能为空')
            db.execute("UPDATE meeting_notes SET expanded_text=?,accepted_text=?,source_segment_ids=?,status='accepted',expansion_revision=?,updated=CURRENT_TIMESTAMP WHERE id=?",
                (result.text,accepted,encode(list(dict.fromkeys(e.segment_id for e in result.evidence))),row['revision'],nid))
        return {'ok':True}


    @app.delete('/api/meetings/{mid}/notes/{nid}')
    def delete_note(mid:str,nid:str):
        meeting(mid)
        if not store.one('SELECT id FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid)):raise HTTPException(404,'简记不存在')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');db.execute('DELETE FROM attention_reviews WHERE note_id=? AND meeting_id=?',(nid,mid));db.execute('DELETE FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid))
        return {'ok':True}


    @app.delete('/api/meetings/{mid}')
    def delete(mid:str):
        meeting(mid)
        busy=store.one('SELECT meeting_id FROM jobs WHERE id=?',(worker.busy_jid,)) if worker.busy_jid else None
        if busy and busy['meeting_id']==mid: raise HTTPException(409,'处理进程正在退出，请稍后删除')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone():
                raise HTTPException(409,'请先取消任务，等待处理停止后删除')
            if db.execute('SELECT 1 FROM meeting_task_links l JOIN note_todos t ON t.id=l.task_id WHERE t.meeting_id=? AND l.meeting_id!=?',(mid,mid)).fetchone():
                raise HTTPException(409,'该会议有带入其他会议的跟进项，请先在关联会议移除关联后再删除')
            db.execute('DELETE FROM terminology WHERE scope=?',(mid,))
            for table in ('live_overview_runs','live_overview_snapshots','live_overview_reviews'):db.execute(f'DELETE FROM {table} WHERE recording_id=?',(mid,))
            db.execute('DELETE FROM live_asr_ranges WHERE recording_id=?',(mid,))
            db.execute('DELETE FROM live_corrections WHERE recording_id=?',(mid,))
            for table in ('live_sessions','live_session_events','live_pcm_inputs'):db.execute(f'DELETE FROM {table} WHERE recording_id=?',(mid,))
            qa_jobs=QA.forget_meeting(db,mid)
            for table in ['minutes_deliveries','minutes_task_bindings','meeting_task_links','minutes_selection','meeting_briefs','attention_reviews','meeting_reviews','note_todos','retrieval_units','meeting_notes','artifacts','revisions','jobs','meetings']:
                key='id' if table=='meetings' else 'meeting_id'
                db.execute(f'DELETE FROM {table} WHERE {key}=?',(mid,))
        for qjid in qa_jobs:shutil.rmtree(store.root/'job-work'/qjid,ignore_errors=True)
        shutil.rmtree(store.directory(mid),ignore_errors=True)
        return {'ok':True}


    @app.get('/api/meetings/{mid}/audio')
    def audio(mid:str):
        row=meeting(mid)
        # Serve normalized PCM when available; supports seeking with HTTP Range.
        paths=sorted(store.directory(mid).glob('*/audio.wav'),key=lambda p:p.stat().st_mtime,reverse=True)
        if paths: return FileResponse(paths[0],media_type='audio/wav')
        return FileResponse(store.directory(mid)/'original',filename=row['filename'],media_type='application/octet-stream',content_disposition_type='inline')


    @app.get('/api/meetings/{mid}/audio-tracks/{track}')
    def audio_track(mid:str,track:Literal['system','microphone']):
        meeting(mid);path=store.directory(mid)/(track+'.wav')
        if not path.is_file():raise HTTPException(404,'这场会议没有该独立音轨')
        return FileResponse(path,media_type='audio/wav',filename=track+'.wav',content_disposition_type='inline')


    @app.post('/api/meetings/{mid}/jobs',status_code=201)
    def enqueue(mid:str,body:JobInput):
        row=meeting(mid)
        # One durable handoff per recording. Retries after response loss return the
        # original job, even after completion/cancellation; explicit reprocessing
        # continues to use ordinary jobs and never silently changes the first plan.
        jid=hashlib.sha256(('recording-handoff:'+mid).encode()).hexdigest()[:32] if body.recording_handoff else uid()
        if body.recording_handoff:
            if not (recordings.root/mid/'session.json').exists():raise HTTPException(409,'会后交接仅适用于本机录音')
            existing=store.one('SELECT id FROM jobs WHERE id=? AND meeting_id=?',(jid,mid))
            if existing:return {'id':existing['id'],'reused':True}
        provider=get_asr_provider(body.engine)
        if body.completion_mode!='quick' and not provider.ready(): raise HTTPException(409,f'{provider.label} 尚未就绪，请先安装对应模型')
        if body.completion_mode=='quick' and not (recordings.root/mid/'session.json').exists():raise HTTPException(409,'快速完成仅适用于本机实时录音')
        if body.completion_mode!='quick' and body.diarization and not LocalDiarization().ready(): raise HTTPException(409,'尚未配置本地发言人模型')
        try:followup=prepare_auto_minutes(store,body.minutes_followup) if body.minutes_followup else None
        except (ValueError,RuntimeError) as error:raise HTTPException(409,str(error))
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if body.recording_handoff and db.execute('SELECT id FROM jobs WHERE id=?',(jid,)).fetchone():return {'id':jid,'reused':True}
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone(): raise HTTPException(409,'已有任务正在处理')
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'queued','等待处理',encode({'engine':body.engine,'chunk_seconds':provider.chunk_seconds,'punctuation':provider.needs_punctuation,'diarization':body.diarization,'num_speakers':body.num_speakers,'base_revision':row['revision'],'engine_identity':provider.identity(),'pipeline_version':2,'minutes_followup':followup,'completion_mode':body.completion_mode,'live_snapshot':live_results.snapshot(mid,body.engine) if body.completion_mode=='quick' else []})))
        return {'id':jid,'reused':False}


    @app.post('/api/meetings/{mid}/analysis-jobs',status_code=201)
    def enqueue_analysis(mid:str,body:AnalysisJobInput):return services.analysis_jobs.submit(mid,body)


    def check_attempt(db,jid,request):
        expected=request.headers.get('X-Task-Attempt')
        if expected is None:return # Compatibility callers retain the original API.
        if not expected.isascii() or not expected.isdigit() or len(expected)>10:raise HTTPException(422,'任务版本无效，请刷新后操作')
        current=db.execute("SELECT count(*) FROM job_runtime_events WHERE job_id=? AND action='retry'",(jid,)).fetchone()[0]
        if int(expected)!=current:raise HTTPException(409,'任务已重试，请刷新后再操作；未更改当前任务')

    @app.post('/api/jobs/{jid}/cancel')
    def cancel(jid:str,request:Request):
        if not store.one('SELECT id FROM jobs WHERE id=?',(jid,)): raise HTTPException(404,'任务不存在')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            check_attempt(db,jid,request)
            old=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
            changed=db.execute("UPDATE jobs SET state='cancelled',stage='已取消' WHERE id=? AND state IN ('running','queued')",(jid,)).rowcount
            if changed:db.execute("INSERT INTO job_runtime_events(job_id,action,from_state) VALUES(?,'cancel',?)",(jid,old['state']))
        if changed:worker.cancel_waiting(jid)
        return {'ok':True}


    @app.post('/api/jobs/{jid}/retry')
    def retry(jid:str,request:Request):
        if worker.busy_jid==jid: raise HTTPException(409,'处理进程正在退出，请稍后重试')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not job: raise HTTPException(404,'任务不存在')
            check_attempt(db,jid,request)
            blocked=retry_blocker(store,dict(job),worker)
            if blocked:raise HTTPException(409,blocked)
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(job['meeting_id'],)).fetchone(): raise HTTPException(409,'已有任务正在处理')
            db.execute("UPDATE jobs SET state='queued',error='',progress=0,stage='等待恢复' WHERE id=?",(jid,))
            db.execute("INSERT INTO job_runtime_events(job_id,action,from_state,error_code) VALUES(?,'retry',?,?)",(jid,job['state'],classify(job['error'])['code'] if job['error'] else None))
        return {'ok':True}


    @app.put('/api/meetings/{mid}/transcript')
    def save(mid:str,body:RevisionInput):
        row=meeting(mid)
        values=[x.model_dump() for x in body.segments]
        for value in values:
            for key in ('timestamp_precision','alignment_warning','continuation_id','continues_previous','continues_next','boundary_reason'):
                if not value.get(key):value.pop(key,None)
        if len({x['id'] for x in values})!=len(values): raise HTTPException(400,'片段 ID 重复')
        if any(x['end']<x['start'] or (row['duration'] and x['end']>row['duration']+1) for x in values): raise HTTPException(400,'时间戳超出录音范围')
        if any(values[i]['start']>values[i+1]['start'] for i in range(len(values)-1)): raise HTTPException(400,'片段必须按时间排序')
        try: revision=store.save_revision(mid,values,'manual',expected=body.revision)
        except ValueError as e: raise HTTPException(409,str(e))
        return {'revision':revision}


    @app.get('/api/meetings/{mid}/revisions/{rev}')
    def revision(mid:str,rev:int):
        meeting(mid)
        row=store.one('SELECT * FROM revisions WHERE meeting_id=? AND revision=?',(mid,rev))
        if not row: raise HTTPException(404,'版本不存在')
        row['segments']=json.loads(row['segments']); return row


    @app.get('/api/meetings/{mid}/export/{format}')
    def export(mid:str,format:str):
        row=meeting(mid); segments=json.loads(row['segments'])
        if format not in ('txt','md','srt','json'): raise HTTPException(400,'不支持此导出格式')
        def stamp(seconds,srt=False):
            ms=round(seconds*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
            return f'{h:02}:{m:02}:{s:02}'+(f',{ms:03}' if srt else '')
        if format=='json': content=encode({'meeting':row['title'],'revision':row['revision'],'segments':segments})
        elif format=='srt': content='\n\n'.join(f"{i+1}\n{stamp(s['start'],True)} --> {stamp(s['end'],True)}\n{s['speaker']}：{s['text']}" for i,s in enumerate(segments))
        else:
            content=('## ' if format=='md' else '')+row['title']+'\n\n'+'\n\n'.join(f"[{stamp(s['start'])}] {s['speaker']}\n{s['text']}" for s in paragraphs(segments))
        filename=quote(row['title'],safe='')+'.'+format
        return Response(content,media_type='application/json' if format=='json' else 'text/plain',headers={'Content-Disposition':f"attachment; filename*=UTF-8''{filename}"})


    @app.get('/api/meetings/{mid}/analysis/export/{format}')
    def export_analysis(mid:str,format:str):
        row=meeting(mid)
        def analysis_stamp(seconds):
            value=max(0,int(seconds));hours,value=divmod(value,3600);minutes,seconds=divmod(value,60)
            return (f'{hours:02}:' if hours else '')+f'{minutes:02}:{seconds:02}'
        if format not in ('md','json'):raise HTTPException(400,'分析仅支持 Markdown 或 JSON')
        artifact=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
        if not artifact:raise HTTPException(404,'当前文稿版本还没有会议分析')
        payload=json.loads(artifact['payload']);provenance=json.loads(artifact['provenance'] or '{}')
        if format=='json':content=encode({'meeting':row['title'],'revision':row['revision'],'analysis':payload,'provenance':provenance})
        else:
            segments={s['id']:s for s in json.loads(row['segments'])}
            labels={'summary':'会议概述','key_points':'关键讨论','decisions':'已定事项','action_items':'待办事项','open_questions':'待确认问题'}
            lines=[f"# {row['title']} · 会议分析",'',f"基于文字稿 v{row['revision']} · {provenance.get('model','本地模型')}",'']
            for section in SECTION_NAMES:
                lines.extend([f"## {labels[section]}",''])
                items=payload.get(section,[])
                if not items:lines.extend(['- 无明确内容','']);continue
                for item in items:
                    refs=[]
                    for sid in item.get('source_segment_ids',[]):
                        if sid in segments:refs.append(analysis_stamp(segments[sid]['start']))
                    details=[]
                    if item.get('owner'):details.append('负责人：'+item['owner'])
                    if item.get('due_date'):details.append('截止：'+item['due_date'])
                    suffix=('；'.join(details)+'；' if details else '')+('依据：'+', '.join(refs) if refs else '')
                    lines.append(f"- {item['content']}"+(f"（{suffix}）" if suffix else ''))
                lines.append('')
            content='\n'.join(lines)
        filename=quote(row['title']+'-会议分析.'+format,safe='')
        return Response(content,media_type='application/json' if format=='json' else 'text/markdown',headers={'Content-Disposition':f"attachment; filename*=UTF-8''{filename}"})
