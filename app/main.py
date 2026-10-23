import asyncio, sqlite3, io, hashlib, json, os, shutil, subprocess, tempfile, threading, time, wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, quote
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, Response, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator
from .db import Store, uid, encode
from .worker import Worker
from .providers import WhisperCpp, get_asr_provider, engine_catalog, LocalDiarization, LocalPunctuation
from .analysis import LMStudioProvider,OpenAICompatibleProvider,MeetingAnalysis,validate_llm_endpoint,public_llm_config,analysis_chunk_chars,TEMPLATE_VERSION,SECTION_NAMES
from .minutes import MeetingMinutes,build_minutes,build_ai_minutes,apply_polished_minutes,validate_minutes_citations,minutes_markdown,minutes_docx,minutes_pdf,MINUTES_VERSION
from .minutes_templates import MinutesTemplate
from .speaker_review import generate_review
from .recordings import RecordingStore
from .native_capture import NativeCapture
from .live_results import LiveResults
from .live_stream import inspect_audio, qwen_events
from .qwen_resident import QwenResident
from .keep_awake import KeepAwake
from .recording_health import RecordingHealth
from .meeting_review import MeetingReview
from .qa import QA
from . import meeting_flow
from .local_embedding import local_embedding
from .retrieval import Retrieval

BASE=Path(__file__).resolve().parents[1]

class Segment(BaseModel):
    id: str=Field(min_length=1,max_length=100)
    start: float=Field(ge=0,allow_inf_nan=False)
    end: float=Field(ge=0,allow_inf_nan=False)
    text: str=Field(max_length=20000)
    speaker: str=Field(default='未分配',max_length=100)
    boundary_review: bool=False
    timestamp_precision: Literal['chunk','aligned'] | None=None
    alignment_warning: str=Field(default='',max_length=300)
    speaker_locked: bool=False
class RevisionInput(BaseModel):
    revision: int
    segments: list[Segment]=Field(max_length=50000)
class JobInput(BaseModel):
    completion_mode: Literal["full","quick"]="full"
    engine: Literal["whisper","qwen"]="whisper"
    diarization: bool=False
    num_speakers: int=Field(default=0,ge=0,le=30)
class AnalysisJobInput(BaseModel):
    profile_id: str | None=Field(default=None,max_length=100)
    model: str | None=Field(default=None,max_length=500)
class SpeakerReviewApplyInput(BaseModel):
    revision: int
    segment_ids: list[str]=Field(min_length=1,max_length=500)
class LLMConfigInput(BaseModel):
    config_id: str | None=Field(default=None,max_length=100)
    name: str=Field(min_length=1,max_length=100)
    provider_type: Literal['local','cloud']
    base_url: str=Field(min_length=1,max_length=1000)
    model: str=Field(min_length=1,max_length=500)
    context_window: int=Field(default=32768,ge=4096,le=2_000_000)
    analysis_chunk_mode: Literal['auto','quality','speed','custom']='auto'
    analysis_chunk_chars: int=Field(default=0,ge=0,le=1_000_000)
    api_key: str | None=Field(default=None,max_length=10000)
    is_default: bool=False
    clear_api_key: bool=False
class RenameInput(BaseModel):
    title: str=Field(min_length=1,max_length=200)
class MeetingNoteInput(BaseModel):
    captured_at: float=Field(default=0,ge=0,allow_inf_nan=False)
    note_type: Literal['note','highlight','question','decision','action']='note'
    raw_text: str=Field(default='',max_length=5000)
    note_scope: Literal['personal','meeting']='personal'
    is_marker: bool=False
    @model_validator(mode='after')
    def valid_note(self):
        if not self.raw_text.strip() and not self.is_marker:raise ValueError('简记内容不能为空，或使用标记此刻')
        return self
class MeetingNotesBatchInput(BaseModel):
    notes: list[MeetingNoteInput]=Field(max_length=1000)
class RetrievalConfigInput(BaseModel):
    provider: Literal['external','builtin']='external'
    profile_id: str=Field(default='',max_length=100)
    model: str=Field(default='',max_length=500)
class SearchInput(BaseModel):
    query: str=Field(min_length=1,max_length=500)
    mode: Literal['keyword','semantic','hybrid']='keyword'
    meeting_id: str | None=None
    kind: Literal['transcript','note','expanded_note','minutes'] | None=None
class SearchResolveInput(BaseModel):
    id: str=Field(min_length=1,max_length=1000)
    fingerprint: str=Field(min_length=64,max_length=64)
class IndexBuildInput(BaseModel):
    force: bool=False
class QAPrepareInput(BaseModel):
    question: str=Field(min_length=1,max_length=500)
    profile_id: str=Field(min_length=1,max_length=100)
    query: str=Field(default='',max_length=500)
    mode: Literal['keyword','semantic','hybrid']='keyword'
    meeting_id: str | None=None
    kind: Literal['transcript','note','expanded_note','minutes'] | None='transcript'
    include_personal: bool=False
class QAGenerateInput(BaseModel):
    confirm_cloud: bool=False

class NoteAcceptInput(BaseModel):
    artifact_id: str=Field(min_length=1,max_length=100)
    accepted_text: str | None=Field(default=None,min_length=1,max_length=3000)

class NoteUseInput(BaseModel):
    kind: Literal['minutes','todo']
    fingerprint: str=Field(min_length=64,max_length=64)
    revision: int=Field(ge=0)
    content: str=Field(min_length=1,max_length=2000)
    source_segment_ids: list[str]=Field(default_factory=list,max_length=20)
    section: Literal['conclusions','decisions','action_items','open_questions']='conclusions'
    minutes_id: str | None=None
    confirmed: bool=False
class TodoUpdate(BaseModel):
    status: Literal['pending','done']
    owner: str=Field(default='',max_length=200)
    due_date: str=Field(default='',max_length=100)
class MeetingNoteUpdate(BaseModel):
    note_type: Literal['note','highlight','question','decision','action']
    raw_text: str=Field(max_length=5000)
    note_scope: Literal['personal','meeting'] | None=None
    is_marker: bool | None=None
    captured_at: float | None=Field(default=None,ge=0,allow_inf_nan=False)
class MeetingBriefInput(BaseModel):
    title: str=Field(default='',max_length=200)
    purpose: str=Field(default='',max_length=1000)
    agenda: str=Field(default='',max_length=3000)
    participants: str=Field(default='',max_length=1000)
    questions: str=Field(default='',max_length=2000)
    previous_meeting_id: str | None=Field(default=None,max_length=100)
class AttentionDecisionInput(BaseModel):
    decision: Literal['meeting','personal','deferred']
    fingerprint: str=Field(min_length=64,max_length=64)
    revision: int=Field(ge=0)
    content: str=Field(default='',max_length=3000)
    source_segment_ids: list[str]=Field(default_factory=list,max_length=20)
    confirmed: bool=False
class ActionTrackInput(BaseModel):
    content: str=Field(min_length=1,max_length=2000)
    owner: str=Field(default='',max_length=200)
    due_date: str=Field(default='',max_length=200)
    confirmed: bool=False
class RecordingInput(BaseModel):
    mime_type: Literal['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/mp4;codecs=mp4a.40.2']='audio/webm'
class RecordingMetadata(MeetingNotesBatchInput):
    brief: MeetingBriefInput | None=None
    duration: float=Field(default=0,ge=0,allow_inf_nan=False)
    chunk_count: int=Field(default=0,ge=0,le=100000)
class RecordingFinish(BaseModel):
    chunk_count: int=Field(ge=1,le=100000)
class NativeDraftInput(BaseModel):
    engine: Literal["whisper","qwen"]="whisper"
    offset: float=Field(default=0,ge=0,le=86400,allow_inf_nan=False)
    responsive: bool=False
    window_seconds: Literal[5,10,15]=15
class KeepAwakeInput(BaseModel):
    enabled: bool=True
class ReviewCheckpointInput(BaseModel):
    fingerprint: str=Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['confirmed','skipped']='confirmed'
class NativeRecordingInput(BaseModel):
    microphone: bool=True
    keep_awake: bool=True
class NativeCommandInput(BaseModel):
    command: Literal['pause','resume','stop']
class MinutesGenerateInput(BaseModel):
    style: Literal['formal','brief','internal']='formal'
    template_id: str=Field(default='general-meeting',max_length=100)
class MinutesSaveInput(BaseModel):
    source_minutes_id: str | None=Field(default=None,max_length=100)
    source_analysis_id: str | None=Field(default=None,max_length=100)
    document: MeetingMinutes
class MinutesPolishInput(BaseModel):
    profile_id: str | None=Field(default=None,max_length=100)
    model: str | None=Field(default=None,max_length=500)
class AIMinutesGenerateInput(MinutesPolishInput):
    mode: Literal['quick','full']='quick'
    template_id: str=Field(default='general-meeting',max_length=100)


def create_app(data_dir=None, start_worker=True):
    store=Store(data_dir or os.getenv('MEETING_DATA',str(BASE/'data')))
    worker=Worker(store)
    retrieval=Retrieval(store)
    @asynccontextmanager
    async def lifespan(app):
        loop=asyncio.get_running_loop()
        worker.asr_lock=live_asr_lock
        worker.release_model=lambda:asyncio.run_coroutine_threadsafe(resident.close(),loop).result(timeout=10)
        if start_worker: worker.start()
        resident.start_monitor()
        store.execute("UPDATE live_asr_ranges SET state='failed',error='服务重启，识别未完成；快速完成会补识别该范围' WHERE state='running'")
        native_capture.start_monitor()
        keep_awake.start_monitor()
        yield
        await run_in_threadpool(keep_awake.close)
        await run_in_threadpool(native_capture.close)
        await run_in_threadpool(worker.close)
        await resident.shutdown()
        local_embedding().cancel.set()
        local_embedding().release()
    app=FastAPI(title='听记 · Local Meeting',lifespan=lifespan)
    app.state.store=store; app.state.worker=worker
    recordings=RecordingStore(store)
    native_capture=NativeCapture(store,recordings)
    def recording_open(rid):
        try:return recordings.read(rid).get('state')!='completed' and not store.one('SELECT id FROM meetings WHERE id=?',(rid,))
        except (FileNotFoundError,ValueError):return False
    keep_awake=KeepAwake(recording_open,lambda rid:getattr(native_capture,'is_running',lambda _:False)(rid))
    app.state.keep_awake=keep_awake
    recording_health=RecordingHealth(store,recordings,native_capture)
    meeting_review=MeetingReview(store)
    qa=QA(store)
    live_results=LiveResults(store,recordings)
    live_asr_lock=threading.Lock()
    resident=QwenResident(store.root)

    @app.middleware('http')
    async def local_only(request:Request,call_next):
        host=request.headers.get('host','').split(':')[0]
        if host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'detail':'只允许本机访问'},status_code=403)
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail':'不允许跨站请求'},status_code=403)
        result=await call_next(request)
        result.headers['X-Content-Type-Options']='nosniff'
        result.headers['Referrer-Policy']='no-referrer'
        return result

    def meeting(mid):
        row=store.one('SELECT * FROM meetings WHERE id=?',(mid,))
        if not row: raise HTTPException(404,'会议不存在')
        return row
    def active(mid):
        return store.one("SELECT * FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,))
    def minutes_template(template_id):
        row=store.one("SELECT definition FROM minutes_templates WHERE id=? AND status='active'",(template_id,))
        if not row:raise HTTPException(404,'纪要模板不存在或已停用')
        return MinutesTemplate.model_validate(json.loads(row['definition']))
    def decorated(row):
        row['segments']=json.loads(row['segments'])
        row['jobs']=store.all('SELECT * FROM jobs WHERE meeting_id=? ORDER BY created DESC,rowid DESC',(row['id'],))
        row['revisions']=store.all('SELECT revision,created,source FROM revisions WHERE meeting_id=? ORDER BY revision DESC',(row['id'],))
        artifacts=store.all('SELECT * FROM artifacts WHERE meeting_id=? ORDER BY created DESC,rowid DESC',(row['id'],))
        for a in artifacts:
            a['payload']=json.loads(a['payload']);a['provenance']=json.loads(a['provenance'] or '{}');a['stale']=a['revision'] != row['revision']
        with store.connect() as db:
            for artifact in artifacts:
                if artifact['kind']=='meeting_minutes':artifact['stale']=artifact['stale'] or not meeting_flow.artifact_current(db,row['id'],artifact['provenance'])
        row['artifacts']=artifacts
        notes=store.all('SELECT * FROM meeting_notes WHERE meeting_id=? ORDER BY captured_at,created,rowid',(row['id'],))
        for note in notes:
            note['source_segment_ids']=json.loads(note['source_segment_ids'] or '[]')
            note['expansion_stale']=bool(note['expansion_revision'] and note['expansion_revision']!=row['revision'])
            suggestion=next((a for a in artifacts if a['kind']=='note_expansion' and a['payload'].get('note_id')==note['id']),None)
            if suggestion:
                suggestion['stale']=suggestion['stale'] or any(suggestion['payload'].get(key)!=note[key] for key in ('raw_text','note_type','captured_at')) or suggestion['payload'].get('note_scope','personal')!=note['note_scope']
            note['expansion']=suggestion
        row['notes']=notes
        from .notes import fingerprint
        todos=store.all('SELECT * FROM note_todos WHERE meeting_id=? ORDER BY created,rowid',(row['id'],))
        by_id={n['id']:n for n in notes}
        current_minutes=next((a for a in artifacts if a['kind']=='meeting_minutes' and a['revision']==row['revision'] and not a['stale']),None)
        for note in notes:
            note['adopted_to_minutes']=bool(current_minutes and any(item.get('note_id')==note['id'] and item.get('note_fingerprint')==fingerprint(note) for item in current_minutes['provenance'].get('adopted_notes',[])))
        with store.connect() as db:
            db.execute('BEGIN')
            for todo in todos:
                todo['source_segment_ids']=json.loads(todo['source_segment_ids'])
                todo['stale']=meeting_flow.todo_stale(db,todo)
        row['note_todos']=todos
        row['audio_tracks']=[name for name in ('system','microphone') if (store.directory(row['id'])/(name+'.wav')).is_file()]
        row['review']=meeting_review.read(row['id'])
        row['flow']=meeting_flow.flow(store,row['id'])
        return row

    @app.put('/api/meetings/{mid}/review/{step}')
    def confirm_review(mid:str,step:Literal['transcript','speakers','notes','minutes'],body:ReviewCheckpointInput):
        try:return meeting_review.set(mid,step,body.fingerprint,body.decision)
        except KeyError:raise HTTPException(404,'会议不存在')
        except ValueError as error:raise HTTPException(409,str(error))
    @app.delete('/api/meetings/{mid}/review/{step}')
    def reset_review(mid:str,step:Literal['transcript','speakers','notes','minutes']):
        try:return meeting_review.reset(mid,step)
        except KeyError:raise HTTPException(404,'会议不存在')

    @app.get('/api/retrieval')
    def retrieval_status():return retrieval.status()
    @app.post('/api/retrieval/qa/prepare',status_code=201)
    def qa_prepare(body:QAPrepareInput):
        if not body.question.strip():raise HTTPException(422,'请输入问题')
        acquired=False
        try:
            if body.mode!='keyword':
                if worker.busy_jid or not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'模型正在处理任务，请先使用关键词检索')
                acquired=True
                if worker.release_model:worker.release_model()
            return qa.prepare(**body.model_dump())
        except (RuntimeError,ValueError,ImportError,sqlite3.Error) as error:raise HTTPException(409,str(error))
        finally:
            if acquired:live_asr_lock.release()
    @app.get('/api/retrieval/qa')
    def qa_history():return qa.history()
    @app.get('/api/retrieval/qa/{qid}')
    def qa_read(qid:str):
        try:return qa.read(qid)
        except KeyError:raise HTTPException(404,'问答记录不存在或来源会议已删除')
    @app.post('/api/retrieval/qa/{qid}/generate',status_code=201)
    def qa_generate(qid:str,body:QAGenerateInput):
        try:return qa.enqueue(qid,body.confirm_cloud)
        except KeyError:raise HTTPException(404,'问答记录不存在')
        except ValueError as error:raise HTTPException(409,str(error))
    @app.delete('/api/retrieval/qa/{qid}')
    def qa_delete(qid:str):
        try:qa.delete(qid);return {'ok':True}
        except KeyError:raise HTTPException(404,'问答记录不存在')
    @app.post('/api/retrieval/builtin/install',status_code=202)
    def builtin_install():
        try:return local_embedding().start_install()
        except ValueError as error:raise HTTPException(409,str(error))
    @app.post('/api/retrieval/builtin/cancel')
    def builtin_cancel():
        local_embedding().cancel.set();return {'ok':True}
    @app.post('/api/retrieval/builtin/import')
    def builtin_import(file:UploadFile=File(...)):
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'model.zip';size=0
                with path.open('wb') as output:
                    while block:=file.file.read(1024*1024):
                        size+=len(block)
                        if size>26*1024*1024:raise HTTPException(413,'离线模型包超过 26 MiB 上限')
                        output.write(block)
                return local_embedding().import_zip(path)
        except (ValueError,OSError) as error:raise HTTPException(409,str(error))
        finally:file.file.close()
    @app.put('/api/retrieval/config')
    def retrieval_configure(body:RetrievalConfigInput):
        profile=store.one('SELECT * FROM llm_configs WHERE id=?',(body.profile_id,))
        if body.provider=='external' and not profile:raise HTTPException(404,'本地模型配置不存在')
        if worker.busy_jid or not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'正在处理录音或正式任务，请稍后配置向量模型')
        try:
            if worker.release_model:worker.release_model()
            return retrieval.configure_builtin() if body.provider=='builtin' else retrieval.configure(profile,body.model.strip())
        except (RuntimeError,ValueError,ImportError) as error:raise HTTPException(409,str(error))
        finally:live_asr_lock.release()
    @app.post('/api/retrieval/build',status_code=201)
    def retrieval_build(body:IndexBuildInput):
        status=retrieval.status();config=status['config']
        if not config or not status['vec_ready'] or status['error']:raise HTTPException(409,status['error'] or '请先配置本地向量模型')
        try:retrieval.profile(config)
        except ValueError as error:raise HTTPException(409,str(error))
        jid=uid()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE kind='retrieval_index' AND state IN ('running','queued')").fetchone():raise HTTPException(409,'索引任务已在处理中')
            if body.force:db.execute("UPDATE retrieval_units SET embedding=NULL,identity=''")
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)',(jid,'','queued','等待建立检索索引',encode(config),'retrieval_index'))
        return {'id':jid}
    @app.post('/api/retrieval/search')
    def retrieval_search(body:SearchInput):
        if not body.query.strip():raise HTTPException(422,'请输入搜索内容')
        acquired=False
        try:
            if body.mode!='keyword':
                if worker.busy_jid or not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'正在处理录音或正式任务，请先使用关键词搜索')
                acquired=True
                if worker.release_model:worker.release_model()
            return retrieval.search(body.query,body.mode,meeting_id=body.meeting_id,kind=body.kind)
        except (RuntimeError,ValueError,ImportError,sqlite3.Error) as error:raise HTTPException(409,str(error))
        finally:
            if acquired:live_asr_lock.release()
    @app.post('/api/retrieval/resolve')
    def retrieval_resolve(body:SearchResolveInput):
        try:return retrieval.resolve(body.id,body.fingerprint)
        except ValueError as error:raise HTTPException(409,str(error))

    @app.get('/api/status')
    def status():
        engine=WhisperCpp(); diar=LocalDiarization()
        profiles=[public_llm_config(row) for row in store.all('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid')]
        if profiles:
            default=next((row for row in profiles if row['is_default']),profiles[0])
            analysis={'ready':True,'models':[row['model'] for row in profiles],'error':'','base_url':default['base_url'],'profiles':profiles,'default_profile_id':default['id']}
        else:
            try:analysis=LMStudioProvider().status()
            except ValueError as error:analysis={'ready':False,'models':[],'error':str(error),'base_url':''}
            analysis.update(profiles=[],default_profile_id=None)
        return {'engines':engine_catalog(),'default_engine':'whisper','engine_ready':engine.ready(),'engine':'whisper.cpp / Metal','model':Path(engine.model).name,'diarization_ready':diar.ready(),'punctuation_ready':LocalPunctuation().ready(),'ffmpeg_ready':bool(shutil.which('ffmpeg')),'data_dir':str(store.root),'offline':True,'analysis':analysis,'qa_ready':True,'meeting_flow_ready':True}

    @app.get('/api/minutes-templates')
    def list_minutes_templates():
        return [json.loads(row['definition']) for row in store.all(
            "SELECT definition FROM minutes_templates WHERE status='active' ORDER BY builtin DESC,rowid")]

    @app.get('/api/minutes-templates/{template_id}')
    def get_minutes_template(template_id:str):
        return minutes_template(template_id).model_dump()

    @app.post('/api/live-transcribe')
    async def live_transcribe(file:UploadFile=File(...),offset:float=Form(default=0,ge=0,le=86400,allow_inf_nan=False),
                              engine:Literal['whisper','qwen']=Form(default='whisper'),
                              recording_id:str|None=Form(default=None),source:Literal['browser','native']=Form(default='browser')):
        started=time.monotonic();aid=None;locked=False
        try:
            provider=get_asr_provider(engine)
            payload=await file.read(4*1024*1024+1)
            if len(payload)>4*1024*1024:raise HTTPException(413,'实时音频片段最大支持 4MB')
            if not payload:raise HTTPException(400,'实时音频片段为空')
            try:
                with wave.open(io.BytesIO(payload),'rb') as wav:
                    duration=wav.getnframes()/wav.getframerate()
                    valid=wav.getnchannels()==1 and wav.getsampwidth()==2 and wav.getframerate()==16000
                    samples=wav.readframes(wav.getnframes())
                    if len(samples)!=wav.getnframes()*2:valid=False
            except (wave.Error,EOFError):raise HTTPException(400,'实时音频必须是有效的 WAV 文件')
            if not valid:raise HTTPException(400,'实时音频必须是 16 kHz 单声道 PCM')
            if duration<.5 or duration>45:raise HTTPException(400,'实时音频片段时长应在 0.5–45 秒之间')
            def response(segments,cached=False):return {'segments':segments,'duration':duration,'engine':engine,
                'processing_ms':round((time.monotonic()-started)*1000),'cached':cached,'persisted':bool(recording_id)}
            if recording_id:
                try:aid,cached=live_results.begin(recording_id,source,engine,offset,duration,payload,provider.identity())
                except FileNotFoundError as error:raise HTTPException(404,str(error))
                except ValueError as error:raise HTTPException(409,str(error))
                if cached is not None:return response(cached,True)
            if not any(samples):segments=[]
            else:
                if not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'上一段实时草稿仍在识别')
                locked=True
                if not provider.ready():raise HTTPException(409,f'{provider.label} 实时草稿模型尚未就绪')
                with tempfile.TemporaryDirectory(prefix='meeting-live-',dir=store.root) as folder_name:
                    folder=Path(folder_name);audio=folder/'chunk.wav';audio.write_bytes(payload);log=folder/(engine+'.log')
                    def run(args):
                        with log.open('ab') as handle:
                            try:result=subprocess.run(args,stdout=handle,stderr=handle,timeout=90,check=False)
                            except subprocess.TimeoutExpired:raise RuntimeError('实时草稿识别超时')
                        if result.returncode:raise RuntimeError('实时草稿识别失败：'+log.read_text(errors='replace')[-800:])
                    try:part=await run_in_threadpool(getattr(provider,'transcribe_live',provider.transcribe),audio,folder/'result',run)
                    except RuntimeError as error:raise HTTPException(503,str(error))
                    segments=[{'start':round(offset+max(0,min(duration,s['start'])),3),
                        'end':round(offset+max(0,min(duration,s['end'])),3),'text':s['text']} for s in part if s['text'].strip()]
            if aid:live_results.complete(aid,segments)
            return response(segments)
        except BaseException as error:
            if aid:live_results.fail(aid,getattr(error,'detail',str(error)) or '识别请求中断')
            raise
        finally:
            await file.close()
            if locked:live_asr_lock.release()

    def stream_response(events):
        return StreamingResponse(events,media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @app.post('/api/live-transcribe/stream')
    async def live_transcribe_stream(file:UploadFile=File(...),offset:float=Form(default=0,ge=0,le=86400,allow_inf_nan=False),
                                    recording_id:str|None=Form(default=None),confirmed:bool=Form(default=True)):
        try:
            payload=await file.read(4*1024*1024+1)
            inspect_audio(payload)
            if recording_id:live_results.check_session(recording_id,'browser')
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(400,str(error))
        finally:await file.close()
        return stream_response(qwen_events(get_asr_provider('qwen'),live_results,live_asr_lock,payload,offset,recording_id,'browser',store.root,resident=resident,confirmed=confirmed))

    def llm_config(cid):
        row=store.one('SELECT * FROM llm_configs WHERE id=?',(cid,))
        if not row:raise HTTPException(404,'模型配置不存在')
        return row

    def checked_config(body):
        try:base_url=validate_llm_endpoint(body.provider_type,body.base_url)
        except ValueError as error:raise HTTPException(400,str(error))
        api_key=(body.api_key or '').strip()
        if body.provider_type=='cloud' and not api_key:raise HTTPException(400,'云端模型需要 API Key')
        try:analysis_chunk_chars(body.context_window,body.analysis_chunk_mode,body.analysis_chunk_chars)
        except ValueError as error:raise HTTPException(400,str(error))
        return base_url,api_key,body.context_window

    @app.get('/api/llm-configs')
    def list_llm_configs():
        return [public_llm_config(row) for row in store.all('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid')]

    @app.post('/api/llm-configs/test')
    def test_llm_config(body:LLMConfigInput):
        try:base_url=validate_llm_endpoint(body.provider_type,body.base_url)
        except ValueError as error:raise HTTPException(400,str(error))
        old=llm_config(body.config_id) if body.config_id else None
        api_key=(body.api_key or '').strip() or (old['api_key'] if old else '')
        if body.provider_type=='cloud' and not api_key:raise HTTPException(400,'云端模型需要 API Key')
        profile={'provider_type':body.provider_type,'base_url':base_url,'model':body.model.strip(),'api_key':api_key}
        provider=OpenAICompatibleProvider(profile);result=provider.status()
        if not result['ready']:raise HTTPException(409,result['error'])
        detected=provider.model_context_window(body.model.strip()) if body.model.strip() in result['models'] else None
        effective=detected or body.context_window
        try:max_chars=analysis_chunk_chars(effective,body.analysis_chunk_mode,body.analysis_chunk_chars)
        except ValueError as error:raise HTTPException(400,str(error))
        return {'ok':True,'models':result['models'],'model_available':body.model.strip() in result['models'],
            'detected_context_window':detected,'effective_context_window':effective,'analysis_max_chars':max_chars}

    @app.post('/api/llm-configs/{cid}/test')
    def test_saved_llm_config(cid:str):
        profile=llm_config(cid);provider=OpenAICompatibleProvider(profile);result=provider.status()
        if not result['ready']:raise HTTPException(409,result['error'])
        detected=provider.model_context_window(profile['model']) if profile['model'] in result['models'] else None
        effective=detected or profile['context_window']
        max_chars=analysis_chunk_chars(effective,profile['analysis_chunk_mode'],profile['analysis_chunk_chars'])
        return {'ok':True,'models':result['models'],'model_available':profile['model'] in result['models'],
            'detected_context_window':detected,'effective_context_window':effective,'analysis_max_chars':max_chars}

    @app.post('/api/llm-configs',status_code=201)
    def create_llm_config(body:LLMConfigInput):
        base_url,api_key,context_window=checked_config(body);cid=uid()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            make_default=body.is_default or not db.execute('SELECT 1 FROM llm_configs LIMIT 1').fetchone()
            if make_default:db.execute('UPDATE llm_configs SET is_default=0')
            db.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key,is_default,context_window,analysis_chunk_mode,analysis_chunk_chars) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (cid,body.name.strip(),body.provider_type,base_url,body.model.strip(),api_key,int(make_default),context_window,body.analysis_chunk_mode,body.analysis_chunk_chars))
        return public_llm_config(llm_config(cid))

    @app.put('/api/llm-configs/{cid}')
    def update_llm_config(cid:str,body:LLMConfigInput):
        old=llm_config(cid)
        try:base_url=validate_llm_endpoint(body.provider_type,body.base_url)
        except ValueError as error:raise HTTPException(400,str(error))
        api_key='' if body.clear_api_key else ((body.api_key or '').strip() or old['api_key'])
        if body.provider_type=='cloud' and not api_key:raise HTTPException(400,'云端模型需要 API Key')
        try:analysis_chunk_chars(body.context_window,body.analysis_chunk_mode,body.analysis_chunk_chars)
        except ValueError as error:raise HTTPException(400,str(error))
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if body.is_default:db.execute('UPDATE llm_configs SET is_default=0')
            db.execute('UPDATE llm_configs SET name=?,provider_type=?,base_url=?,model=?,api_key=?,is_default=?,context_window=?,analysis_chunk_mode=?,analysis_chunk_chars=?,updated=CURRENT_TIMESTAMP WHERE id=?',
                (body.name.strip(),body.provider_type,base_url,body.model.strip(),api_key,int(body.is_default or old['is_default']),body.context_window,body.analysis_chunk_mode,body.analysis_chunk_chars,cid))
        return public_llm_config(llm_config(cid))

    @app.delete('/api/llm-configs/{cid}')
    def delete_llm_config(cid:str):
        old=llm_config(cid)
        for job in store.all("SELECT config FROM jobs WHERE kind='analysis' AND state IN ('queued','running')"):
            if json.loads(job['config']).get('profile_id')==cid:raise HTTPException(409,'该配置正在被分析任务使用')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');db.execute('DELETE FROM llm_configs WHERE id=?',(cid,))
            if old['is_default']:
                replacement=db.execute('SELECT id FROM llm_configs ORDER BY created,rowid LIMIT 1').fetchone()
                if replacement:db.execute('UPDATE llm_configs SET is_default=1 WHERE id=?',(replacement['id'],))
        return {'ok':True}
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
    @app.get('/api/recordings')
    def list_recordings():return [row|{'health':recording_health.snapshot(row['id'])} for row in recordings.list()]
    @app.get('/api/live-model')
    def live_model_status():return resident.status()|{'formal_job_running':bool(worker.busy_jid)}

    @app.post('/api/live-model/{action}')
    async def live_model_control(action:Literal['warm','release']):
        if worker.busy_jid or not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'正式处理或实时识别正在使用资源，请稍后重试')
        try:
            if action=='warm':await resident.warm(get_asr_provider('qwen'))
            else:await resident.close()
            return resident.status()
        except (RuntimeError,TimeoutError) as error:raise HTTPException(409,str(error) or '模型加载超时')
        finally:live_asr_lock.release()

    @app.get('/api/native-capture')
    def native_capability():return native_capture.capability()
    @app.post('/api/native-capture',status_code=201)
    def native_start(body:NativeRecordingInput):
        try:
            session=native_capture.start(body.microphone)
            if body.keep_awake:keep_awake.acquire(session['id'])
            return session
        except (ValueError,OSError) as error:raise HTTPException(409,str(error))
    @app.get('/api/native-capture/{rid}')
    def native_status(rid:str):
        try:return native_capture.status(rid)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
    @app.post('/api/native-capture/{rid}/draft')
    async def native_draft(rid:str,body:NativeDraftInput):
        try:window=await run_in_threadpool(native_capture.draft_window,rid,body.offset)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        if not window['ready']:
            return window|{'segments':[],'duration':0,'processing_ms':0}
        payload=window.pop('payload')
        window.pop('silent')
        result=await live_transcribe(UploadFile(file=io.BytesIO(payload),filename='native-draft.wav'),window['offset'],body.engine,rid,'native')
        return window|result

    @app.post('/api/native-capture/{rid}/draft/stream')
    async def native_draft_stream(rid:str,body:NativeDraftInput):
        if body.engine!='qwen':raise HTTPException(400,'逐字推送仅支持 Qwen，请使用 Whisper 整批接口')
        try:window=await run_in_threadpool(native_capture.draft_window,rid,body.offset,body.window_seconds,body.responsive)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        if not window['ready']:
            async def waiting():
                yield encode({'type':'final','seq':0,'request_id':uid(),'recording_id':rid,'engine':'qwen',
                    'result':window|{'segments':[],'duration':0,'processing_ms':0}})+'\n'
            return stream_response(waiting())
        payload=window.pop('payload');window.pop('silent')
        return stream_response(qwen_events(get_asr_provider('qwen'),live_results,live_asr_lock,payload,window['offset'],rid,'native',store.root,window,resident=resident,confirmed=window.get('confirmed',True)))

    @app.post('/api/native-capture/{rid}/command')
    def native_command(rid:str,body:NativeCommandInput):
        try:return native_capture.command(rid,body.command)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except (ValueError,RuntimeError) as error:raise HTTPException(409,str(error))
    @app.post('/api/native-capture/{rid}/finish')
    def native_finish(rid:str):
        try:
            mid=native_capture.finish(rid);keep_awake.release(rid);return decorated(meeting(mid))
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except (ValueError,RuntimeError,subprocess.SubprocessError) as error:raise HTTPException(409,str(error))
    @app.get('/api/recordings/{rid}/live-results')
    def recording_live_results(rid:str,engine:Literal['whisper','qwen']='whisper'):
        try:return live_results.overview(rid,engine)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
    @app.post('/api/recordings',status_code=201)
    def create_recording(body:RecordingInput):return recordings.create(body.mime_type)
    @app.get('/api/recordings/{rid}/health')
    def recording_inventory(rid:str):
        try:return recording_health.snapshot(rid)
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        except ValueError:raise HTTPException(409,'录音元数据无法读取，已有文件保留')
    @app.get('/api/recordings/{rid}/diagnostics')
    def recording_diagnostics(rid:str):
        report=recording_inventory(rid)
        return JSONResponse(report,headers={'Content-Disposition':'attachment; filename="recording-diagnostics.json"','Cache-Control':'no-store'})
    @app.post('/api/recordings/{rid}/keep-awake')
    def recording_keep_awake(rid:str,body:KeepAwakeInput):
        try:
            recordings.folder(rid)
            if not body.enabled:return keep_awake.release(rid)
            if not recording_open(rid):raise HTTPException(409,'录音已完成或不存在，不能保持亮屏')
            return keep_awake.acquire(rid)
        except (FileNotFoundError,ValueError) as error:raise HTTPException(409,str(error))
    @app.put('/api/recordings/{rid}/metadata')
    def save_recording_metadata(rid:str,body:RecordingMetadata):
        try:return recordings.metadata(rid,[note.model_dump() for note in body.notes],body.duration,body.chunk_count,body.brief.model_dump() if body.brief else None)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
    @app.put('/api/recordings/{rid}/chunks/{index}')
    def save_recording_chunk(rid:str,index:int,file:UploadFile=File(...)):
        try:
            if not 0<=index<100000:raise HTTPException(422,'分段编号无效')
            payload=file.file.read(64*1024*1024+1)
            if not payload:raise HTTPException(400,'录音分段为空')
            if len(payload)>64*1024*1024:raise HTTPException(413,'单个录音分段最大支持 64MB')
            return recordings.chunk(rid,index,payload)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        finally:file.file.close()
    @app.post('/api/recordings/{rid}/finish')
    def finish_recording(rid:str,body:RecordingFinish):
        try:
            mid=recordings.finish(rid,body.chunk_count);keep_awake.release(rid);return decorated(meeting(mid))
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
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
            for todo in value['note_todos']:values.append({**todo,'meeting_id':row['id'],'meeting_title':row['title']})
        return values
    @app.post('/api/meetings/{mid}/speaker-reviews',status_code=201)
    def create_speaker_review(mid:str,body:AnalysisJobInput):
        row=meeting(mid);segments=json.loads(row['segments'])
        if not segments:raise HTTPException(409,'请先完成文字稿')
        speakers={s['speaker'] for s in segments if s.get('speaker') and s['speaker']!='未分配'}
        if len(speakers)<2:raise HTTPException(409,'请先完成声学发言人区分或手动标注至少两位发言人')
        profile=llm_config(body.profile_id) if body.profile_id else store.one('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid LIMIT 1')
        try:
            provider=OpenAICompatibleProvider(profile) if profile else LMStudioProvider()
            model=provider.resolve_model(body.model);aid=uid()
            max_chars=analysis_chunk_chars(profile['context_window'],profile['analysis_chunk_mode'],profile['analysis_chunk_chars']) if profile else 8000
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
        from .notes import insert_note
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
            from .notes import insert_note
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
        from .notes import review
        try:return review(store,mid,nid)
        except ValueError as error:raise HTTPException(404,str(error))
    @app.post('/api/meetings/{mid}/notes/{nid}/use')
    def use_meeting_note(mid:str,nid:str,body:NoteUseInput):
        from .notes import use_note
        try:return use_note(store,mid,nid,body)
        except ValueError as error:raise HTTPException(409,str(error))
    @app.post('/api/meetings/{mid}/minutes/{aid}/actions/{index}/track',status_code=201)
    def track_minutes_action(mid:str,aid:str,index:int,body:ActionTrackInput):
        try:return meeting_flow.track_action(store,mid,aid,index,body)
        except ValueError as error:raise HTTPException(409,str(error))
    @app.put('/api/meetings/{mid}/note-todos/{tid}')
    def update_note_todo(mid:str,tid:str,body:TodoUpdate):
        meeting(mid)
        if not store.one('SELECT id FROM note_todos WHERE meeting_id=? AND id=?',(mid,tid)):raise HTTPException(404,'待办不存在')
        store.execute('UPDATE note_todos SET status=?,owner=?,due_date=?,updated=CURRENT_TIMESTAMP WHERE id=?',(body.status,body.owner.strip(),body.due_date.strip(),tid))
        return {'ok':True}
    @app.post('/api/meetings/{mid}/notes/{nid}/expand',status_code=201)
    def expand_note(mid:str,nid:str,body:AnalysisJobInput):
        from .note_expansion import nearby_segments
        row=meeting(mid)
        note=store.one('SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?',(nid,mid))
        if not note:raise HTTPException(404,'简记不存在')
        if not note['raw_text'].strip():raise HTTPException(409,'时间标记需要先补充内容，再请求扩写')
        if not row['revision']:raise HTTPException(409,'请先完成文字稿，再关联依据扩写')
        profile=None
        if body.profile_id:profile=llm_config(body.profile_id)
        elif store.one('SELECT id FROM llm_configs LIMIT 1'):
            profile=store.one('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid LIMIT 1')
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
        from .note_expansion import Expansion,validate_expansion
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
            db.execute('DELETE FROM live_asr_ranges WHERE recording_id=?',(mid,))
            QA.forget_meeting(db,mid)
            for table in ['meeting_briefs','attention_reviews','meeting_reviews','note_todos','retrieval_units','meeting_notes','artifacts','revisions','jobs','meetings']:
                key='id' if table=='meetings' else 'meeting_id'
                db.execute(f'DELETE FROM {table} WHERE {key}=?',(mid,))
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
        provider=get_asr_provider(body.engine)
        if body.completion_mode!='quick' and not provider.ready(): raise HTTPException(409,f'{provider.label} 尚未就绪，请先安装对应模型')
        if body.completion_mode=='quick' and not (recordings.root/mid/'session.json').exists():raise HTTPException(409,'快速完成仅适用于本机实时录音')
        if body.completion_mode!='quick' and body.diarization and not LocalDiarization().ready(): raise HTTPException(409,'尚未配置本地发言人模型')
        jid=uid()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone(): raise HTTPException(409,'已有任务正在处理')
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'queued','等待处理',encode({'engine':body.engine,'chunk_seconds':provider.chunk_seconds,'punctuation':provider.needs_punctuation,'diarization':body.diarization,'num_speakers':body.num_speakers,'base_revision':row['revision'],'engine_identity':provider.identity(),'pipeline_version':2,'completion_mode':body.completion_mode,'live_snapshot':live_results.snapshot(mid,body.engine) if body.completion_mode=='quick' else []})))
        return {'id':jid}
    @app.post('/api/meetings/{mid}/analysis-jobs',status_code=201)
    def enqueue_analysis(mid:str,body:AnalysisJobInput):
        row=meeting(mid)
        if not row['revision']:raise HTTPException(409,'请先完成并校对文字稿')
        profile=None
        if body.profile_id:profile=llm_config(body.profile_id)
        elif store.one('SELECT id FROM llm_configs LIMIT 1'):
            profile=store.one('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid LIMIT 1')
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
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone():raise HTTPException(409,'已有任务正在处理')
            context_window=int(profile['context_window']) if profile else 32768
            chunk_mode=profile['analysis_chunk_mode'] if profile else 'auto';custom_chars=int(profile['analysis_chunk_chars']) if profile else 0
            config={'revision':row['revision'],'profile_id':profile['id'] if profile else None,'provider':provider_snapshot,'model':model,
                'template_version':TEMPLATE_VERSION,'context_window':context_window,'analysis_chunk_mode':chunk_mode,
                'max_chars':analysis_chunk_chars(context_window,chunk_mode,custom_chars)}
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)',
                (jid,mid,'queued','等待分析',encode(config),'analysis'))
        return {'id':jid,'model':model,'revision':row['revision']}
    @app.post('/api/jobs/{jid}/cancel')
    def cancel(jid:str):
        if not store.one('SELECT id FROM jobs WHERE id=?',(jid,)): raise HTTPException(404,'任务不存在')
        store.execute("UPDATE jobs SET state='cancelled',stage='已取消' WHERE id=? AND state IN ('running','queued')",(jid,)); return {'ok':True}
    @app.post('/api/jobs/{jid}/retry')
    def retry(jid:str):
        if worker.busy_jid==jid: raise HTTPException(409,'处理进程正在退出，请稍后重试')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not job: raise HTTPException(404,'任务不存在')
            if job['state'] not in ('failed','cancelled'): raise HTTPException(409,'此任务不能重试')
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(job['meeting_id'],)).fetchone(): raise HTTPException(409,'已有任务正在处理')
            db.execute("UPDATE jobs SET state='queued',error='',stage='等待恢复' WHERE id=?",(jid,))
        return {'ok':True}
    @app.put('/api/meetings/{mid}/transcript')
    def save(mid:str,body:RevisionInput):
        row=meeting(mid)
        values=[x.model_dump() for x in body.segments]
        for value in values:
            for key in ('timestamp_precision','alignment_warning'):
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
            content=('## ' if format=='md' else '')+row['title']+'\n\n'+'\n\n'.join(f"[{stamp(s['start'])}] {s['speaker']}\n{s['text']}" for s in segments)
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

    def minutes_source(mid,row,analysis_id=None):
        if analysis_id:
            artifact=store.one("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND revision=? AND kind='meeting_analysis'",(analysis_id,mid,row['revision']))
        else:
            artifact=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
        if not artifact:raise HTTPException(409,'请先为当前文字稿生成会议分析')
        return artifact

    def public_artifact(a):
        a=dict(a);a['payload']=json.loads(a['payload']);a['provenance']=json.loads(a['provenance'] or '{}')
        if a['kind']=='meeting_minutes':
            with store.connect() as db:a['stale']=not meeting_flow.artifact_current(db,a['meeting_id'],a['provenance']) or meeting(a['meeting_id'])['revision']!=a['revision']
        return a

    @app.post('/api/meetings/{mid}/minutes',status_code=201)
    def generate_minutes(mid:str,body:MinutesGenerateInput):
        row=meeting(mid);source=minutes_source(mid,row)
        segments=json.loads(row['segments'])
        analysis=MeetingAnalysis.model_validate(json.loads(source['payload']))
        template=minutes_template(body.template_id)
        document=build_minutes(row['title'],row['created'],segments,analysis,body.style,template)
        aid=uid();provenance={'source_analysis_id':source['id'],'minutes_version':MINUTES_VERSION,'style':body.style,
                              'template_id':template.id,'template_version':template.version,'edited':False}
        store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
            (aid,mid,row['revision'],'meeting_minutes',encode(document.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))

    @app.put('/api/meetings/{mid}/minutes',status_code=201)
    def save_minutes(mid:str,body:MinutesSaveInput):
        row=meeting(mid)
        if body.document.style!='ai':
            if not body.source_analysis_id:raise HTTPException(409,'规则纪要缺少来源会议分析')
            minutes_source(mid,row,body.source_analysis_id)
        segments=json.loads(row['segments']);valid_ids={segment['id'] for segment in segments}
        try:validate_minutes_citations(body.document,valid_ids)
        except ValueError as error:raise HTTPException(400,str(error))
        aid=uid();provenance={'source_analysis_id':body.source_analysis_id,'source_transcript_revision':row['revision'] if body.document.style=='ai' else None,
                              'minutes_version':MINUTES_VERSION,'style':body.document.style,'template_id':body.document.template_id,
                              'template_version':body.document.template_version,'edited':True,'ai_generated':body.document.style=='ai'}
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            if not current or current['revision']!=row['revision']:raise HTTPException(409,'文字稿版本已变化，请刷新后再保存')
            if body.source_minutes_id:
                base=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND revision=? AND kind='meeting_minutes'",(body.source_minutes_id,mid,row['revision'])).fetchone()
                latest=db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision'])).fetchone()
                if not base or not latest or latest['id']!=body.source_minutes_id:raise HTTPException(409,'纪要版本已变化，请刷新后再保存')
                original=json.loads(base['provenance'])
                for key in ('flow_context_hash','reviewed_attention'):
                    if key in original:provenance[key]=original[key]
                adopted=[n for n in original.get('adopted_notes',[]) if any(item.content==n['content'] and item.source_segment_ids==n['source_segment_ids'] for item in getattr(body.document,n['section']))]
                if adopted:provenance['adopted_notes']=adopted
            db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (aid,mid,row['revision'],'meeting_minutes',encode(body.document.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))

    @app.post('/api/meetings/{mid}/minutes/ai',status_code=201)
    def generate_ai_minutes(mid:str,body:AIMinutesGenerateInput):
        row=meeting(mid);segments=json.loads(row['segments'])
        if not segments:raise HTTPException(409,'文字稿为空，无法生成 AI 会议纪要')
        template=minutes_template(body.template_id)
        source_analysis=minutes_source(mid,row) if body.mode=='full' else None
        try:
            with store.connect() as db:context,context_hash=meeting_flow.ai_context(db,mid)
        except ValueError as error:raise HTTPException(409,str(error))
        profile=None
        if body.profile_id:profile=llm_config(body.profile_id)
        elif store.one('SELECT id FROM llm_configs LIMIT 1'):
            profile=store.one('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid LIMIT 1')
        try:
            if profile:
                provider=OpenAICompatibleProvider(profile);model=provider.resolve_model(body.model)
                provider_name=profile['name'];provider_type=profile['provider_type']
            else:
                provider=LMStudioProvider();model=provider.resolve_model(body.model)
                provider_name='LM Studio';provider_type='local'
            work=store.directory(mid)/('ai-minutes-'+uid());work.mkdir(exist_ok=True)
            def run(args,extra_env=None):
                env=os.environ.copy()
                if extra_env:env.update(extra_env)
                completed=subprocess.run(args,capture_output=True,env=env,timeout=660)
                if completed.returncode:
                    detail=(completed.stderr or completed.stdout).decode(errors='replace')[-1800:]
                    raise RuntimeError(f'AI 会议纪要进程失败（{completed.returncode}）：{detail}')
            speakers=list(dict.fromkeys(s['speaker'] for s in segments if s.get('speaker') and s['speaker']!='未分配'))
            result=provider.generate_ai_minutes(model,segments,{'title':row['title'],'date':row['created'][:10],
                'known_attendees':speakers,'minutes_template':template.model_dump(),**context},work/'result',run)
            document=build_ai_minutes(row['created'],result,template)
            validate_minutes_citations(document,{segment['id'] for segment in segments})
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:raise HTTPException(409,str(error))
        aid=uid();provenance={'minutes_version':MINUTES_VERSION,'style':'ai','template_id':template.id,
            'template_version':template.version,'edited':False,'ai_generated':True,
            'flow_context_hash':context_hash,'reviewed_attention':context['reviewed_attention'],
            'generation_mode':body.mode,'source_transcript_revision':row['revision'],'provider':provider_name,'provider_type':provider_type,'model':model}
        if source_analysis:provenance['source_analysis_id']=source_analysis['id']
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            try:current_context_hash=meeting_flow.ai_context(db,mid)[1]
            except ValueError as error:raise HTTPException(409,str(error))
            if not current or current['revision']!=row['revision'] or current_context_hash!=context_hash:raise HTTPException(409,'生成期间原文或会议准备／关注点变化，请重新生成')
            db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (aid,mid,row['revision'],'meeting_minutes',encode(document.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))

    @app.post('/api/meetings/{mid}/minutes/polish',status_code=201)
    def polish_minutes(mid:str,body:MinutesPolishInput):
        row=meeting(mid)
        source=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
        if not source:raise HTTPException(409,'请先生成会议纪要')
        with store.connect() as db:
            if not meeting_flow.artifact_current(db,mid,json.loads(source['provenance'] or '{}')):raise HTTPException(409,'纪要来源已变化，请重新生成后再润色')
            try:polish_context_hash=meeting_flow.ai_context(db,mid)[1]
            except ValueError as error:raise HTTPException(409,str(error))
        document=MeetingMinutes.model_validate(json.loads(source['payload']))
        segments=json.loads(row['segments']);valid_ids={segment['id'] for segment in segments}
        try:validate_minutes_citations(document,valid_ids)
        except ValueError as error:raise HTTPException(400,str(error))
        profile=None
        if body.profile_id:profile=llm_config(body.profile_id)
        elif store.one('SELECT id FROM llm_configs LIMIT 1'):
            profile=store.one('SELECT * FROM llm_configs ORDER BY is_default DESC,created,rowid LIMIT 1')
        try:
            if profile:
                provider=OpenAICompatibleProvider(profile);model=provider.resolve_model(body.model)
                provider_name=profile['name'];provider_type=profile['provider_type']
            else:
                provider=LMStudioProvider();model=provider.resolve_model(body.model)
                provider_name='LM Studio';provider_type='local'
            work=store.directory(mid)/('minutes-polish-'+uid());work.mkdir(exist_ok=True)
            def run(args,extra_env=None):
                env=os.environ.copy()
                if extra_env:env.update(extra_env)
                completed=subprocess.run(args,capture_output=True,env=env,timeout=660)
                if completed.returncode:
                    detail=(completed.stderr or completed.stdout).decode(errors='replace')[-1800:]
                    raise RuntimeError(f'纪要整理进程失败（{completed.returncode}）：{detail}')
            result=provider.polish_minutes(model,document,work/'result',run)
            polished=apply_polished_minutes(document,result)
            validate_minutes_citations(polished,valid_ids)
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:raise HTTPException(409,str(error))
        source_provenance=json.loads(source['provenance'] or '{}')
        aid=uid();provenance={'source_analysis_id':source_provenance.get('source_analysis_id'),'source_minutes_id':source['id'],
            'minutes_version':MINUTES_VERSION,'style':polished.style,'template_id':polished.template_id,
            'template_version':polished.template_version,'edited':False,'llm_polished':True,
            'provider':provider_name,'provider_type':provider_type,'model':model}
        for key in ('flow_context_hash','reviewed_attention'):
            if key in source_provenance:provenance[key]=source_provenance[key]
        adopted=[n for n in source_provenance.get('adopted_notes',[]) if any(item.content==n['content'] and item.source_segment_ids==n['source_segment_ids'] for item in getattr(polished,n['section']))]
        if adopted:provenance['adopted_notes']=adopted
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest=db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision'])).fetchone()
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            try:current_context_hash=meeting_flow.ai_context(db,mid)[1]
            except ValueError as error:raise HTTPException(409,str(error))
            if not current or current['revision']!=row['revision'] or not latest or latest['id']!=source['id'] or current_context_hash!=polish_context_hash:raise HTTPException(409,'润色期间来源已变化，请重新核对')
            db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (aid,mid,row['revision'],'meeting_minutes',encode(polished.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))

    @app.get('/api/meetings/{mid}/minutes/export/{format}')
    def export_minutes(mid:str,format:str,include_citations:bool=False,inline:bool=False):
        if format not in ('md','docx','pdf'):raise HTTPException(400,'会议纪要仅支持 Markdown、Word 或 PDF')
        row=meeting(mid)
        artifact=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
        if not artifact:raise HTTPException(404,'当前文字稿版本还没有会议纪要')
        document=MeetingMinutes.model_validate(json.loads(artifact['payload']));segments=json.loads(row['segments'])
        validate_minutes_citations(document,{segment['id'] for segment in segments})
        if format=='md':
            content=minutes_markdown(document,segments,include_citations)
            media_type='text/markdown; charset=utf-8'
        elif format=='docx':
            content=minutes_docx(document,segments,include_citations)
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            try:content=minutes_pdf(document,segments,include_citations)
            except RuntimeError as error:raise HTTPException(503,str(error))
            media_type='application/pdf'
        filename=quote(document.title+'.'+format,safe='')
        disposition='inline' if inline and format=='pdf' else 'attachment'
        return Response(content,media_type=media_type,headers={
            'Content-Disposition':f"{disposition}; filename*=UTF-8''{filename}",'Cache-Control':'no-store'})
    app.mount('/',StaticFiles(directory=BASE/'app/static',html=True),name='ui')
    return app

app=create_app()
