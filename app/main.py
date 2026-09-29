import hashlib, json, os, shutil, subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, quote
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .db import Store, uid, encode
from .worker import Worker
from .providers import WhisperCpp, get_asr_provider, engine_catalog, LocalDiarization, LocalPunctuation
from .analysis import LMStudioProvider,OpenAICompatibleProvider,MeetingAnalysis,validate_llm_endpoint,public_llm_config,analysis_chunk_chars,TEMPLATE_VERSION,SECTION_NAMES
from .minutes import MeetingMinutes,build_minutes,build_ai_minutes,apply_polished_minutes,validate_minutes_citations,minutes_markdown,minutes_docx,minutes_pdf,MINUTES_VERSION
from .minutes_templates import MinutesTemplate

BASE=Path(__file__).resolve().parents[1]

class Segment(BaseModel):
    id: str=Field(min_length=1,max_length=100)
    start: float=Field(ge=0,allow_inf_nan=False)
    end: float=Field(ge=0,allow_inf_nan=False)
    text: str=Field(max_length=20000)
    speaker: str=Field(default='未分配',max_length=100)
    boundary_review: bool=False
class RevisionInput(BaseModel):
    revision: int
    segments: list[Segment]=Field(max_length=50000)
class JobInput(BaseModel):
    engine: Literal["whisper","qwen"]="whisper"
    diarization: bool=False
    num_speakers: int=Field(default=0,ge=0,le=30)
class AnalysisJobInput(BaseModel):
    profile_id: str | None=Field(default=None,max_length=100)
    model: str | None=Field(default=None,max_length=500)
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
class MinutesGenerateInput(BaseModel):
    style: Literal['formal','brief','internal']='formal'
    template_id: str=Field(default='general-meeting',max_length=100)
class MinutesSaveInput(BaseModel):
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
    @asynccontextmanager
    async def lifespan(app):
        if start_worker: worker.start()
        yield
        worker.close()
    app=FastAPI(title='听记 · Local Meeting',lifespan=lifespan)
    app.state.store=store; app.state.worker=worker

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
        row['artifacts']=artifacts
        return row

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
        return {'engines':engine_catalog(),'default_engine':'whisper','engine_ready':engine.ready(),'engine':'whisper.cpp / Metal','model':Path(engine.model).name,'diarization_ready':diar.ready(),'punctuation_ready':LocalPunctuation().ready(),'ffmpeg_ready':bool(shutil.which('ffmpeg')),'data_dir':str(store.root),'offline':True,'analysis':analysis}

    @app.get('/api/minutes-templates')
    def list_minutes_templates():
        return [json.loads(row['definition']) for row in store.all(
            "SELECT definition FROM minutes_templates WHERE status='active' ORDER BY builtin DESC,rowid")]

    @app.get('/api/minutes-templates/{template_id}')
    def get_minutes_template(template_id:str):
        return minutes_template(template_id).model_dump()

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
    @app.delete('/api/meetings/{mid}')
    def delete(mid:str):
        meeting(mid)
        busy=store.one('SELECT meeting_id FROM jobs WHERE id=?',(worker.busy_jid,)) if worker.busy_jid else None
        if busy and busy['meeting_id']==mid: raise HTTPException(409,'处理进程正在退出，请稍后删除')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone():
                raise HTTPException(409,'请先取消任务，等待处理停止后删除')
            for table in ['artifacts','revisions','jobs','meetings']:
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
    @app.post('/api/meetings/{mid}/jobs',status_code=201)
    def enqueue(mid:str,body:JobInput):
        row=meeting(mid)
        provider=get_asr_provider(body.engine)
        if not provider.ready(): raise HTTPException(409,f'{provider.label} 尚未就绪，请先安装对应模型')
        if body.diarization and not LocalDiarization().ready(): raise HTTPException(409,'尚未配置本地发言人模型')
        jid=uid()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('running','queued')",(mid,)).fetchone(): raise HTTPException(409,'已有任务正在处理')
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'queued','等待处理',encode({'engine':body.engine,'chunk_seconds':provider.chunk_seconds,'punctuation':provider.needs_punctuation,'diarization':body.diarization,'num_speakers':body.num_speakers,'base_revision':row['revision'],'engine_identity':provider.identity(),'pipeline_version':2})))
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
        a=dict(a);a['payload']=json.loads(a['payload']);a['provenance']=json.loads(a['provenance'] or '{}');return a

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
        store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
            (aid,mid,row['revision'],'meeting_minutes',encode(body.document.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))

    @app.post('/api/meetings/{mid}/minutes/ai',status_code=201)
    def generate_ai_minutes(mid:str,body:AIMinutesGenerateInput):
        row=meeting(mid);segments=json.loads(row['segments'])
        if not segments:raise HTTPException(409,'文字稿为空，无法生成 AI 会议纪要')
        template=minutes_template(body.template_id)
        source_analysis=minutes_source(mid,row) if body.mode=='full' else None
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
                'known_attendees':speakers,'minutes_template':template.model_dump()},work/'result',run)
            document=build_ai_minutes(row['created'],result,template)
            validate_minutes_citations(document,{segment['id'] for segment in segments})
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:raise HTTPException(409,str(error))
        aid=uid();provenance={'minutes_version':MINUTES_VERSION,'style':'ai','template_id':template.id,
            'template_version':template.version,'edited':False,'ai_generated':True,
            'generation_mode':body.mode,'source_transcript_revision':row['revision'],'provider':provider_name,'provider_type':provider_type,'model':model}
        if source_analysis:provenance['source_analysis_id']=source_analysis['id']
        store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
            (aid,mid,row['revision'],'meeting_minutes',encode(document.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))

    @app.post('/api/meetings/{mid}/minutes/polish',status_code=201)
    def polish_minutes(mid:str,body:MinutesPolishInput):
        row=meeting(mid)
        source=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
        if not source:raise HTTPException(409,'请先生成会议纪要')
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
        store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
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
