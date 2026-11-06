"""Runtime HTTP routes; shared services are supplied by the composition root."""
import json, os, shutil
from pathlib import Path
from fastapi import HTTPException
from ..db import uid
from ..host_bridge import identity as host_identity
from ..providers import WhisperCpp, engine_catalog, LocalDiarization, LocalPunctuation
from ..analysis import OpenAICompatibleProvider, validate_llm_endpoint, public_llm_config, analysis_chunk_chars
from ..http_schemas import LLMConfigInput
from ..application_services import ApplicationServices

def register_routes(app,services:ApplicationServices):
    llm_config=services.configurations.get
    checked_config=services.configurations.checked
    lifecycle=services.lifecycle
    minutes_template=services.meetings.minutes_template
    store=services.store
    LMStudioProvider=services.providers.llm

    @app.get('/api/runtime/host')
    def host_connection():
        return host_identity(app.state.paths)

    @app.get('/api/runtime/service')
    def service_identity():
        return {'pid':os.getpid(),'instance_id':os.getenv('TINGJI_INSTANCE_ID'),
                'mode':os.getenv('TINGJI_SERVICE_MODE','foreground'),'journal_available':lifecycle.available}


    @app.get('/api/runtime/lifecycle')
    def service_lifecycle():return {'available':lifecycle.available,'events':lifecycle.events(),'mode':os.getenv('TINGJI_SERVICE_MODE','foreground')}


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
        return {'engines':engine_catalog(),'default_engine':'whisper','engine_ready':engine.ready(),'engine':'whisper.cpp / Metal','model':Path(engine.model).name,'diarization_ready':diar.ready(),'punctuation_ready':LocalPunctuation().ready(),'ffmpeg_ready':bool(shutil.which('ffmpeg')),'data_dir':str(store.root),'offline':True,'analysis':analysis,'qa_ready':True,'meeting_flow_ready':True,'auto_minutes_ready':True}


    @app.get('/api/minutes-templates')
    def list_minutes_templates():
        return [json.loads(row['definition']) for row in store.all(
            "SELECT definition FROM minutes_templates WHERE status='active' ORDER BY builtin DESC,rowid")]


    @app.get('/api/minutes-templates/{template_id}')
    def get_minutes_template(template_id:str):
        return minutes_template(template_id).model_dump()


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
        changed=(body.provider_type!=old['provider_type'] or body.base_url.rstrip('/')!=old['base_url'].rstrip('/') or body.model.strip()!=old['model'] or
            body.context_window!=old['context_window'] or body.analysis_chunk_mode!=old['analysis_chunk_mode'] or body.analysis_chunk_chars!=old['analysis_chunk_chars'] or
            body.clear_api_key or bool(body.api_key and body.api_key.strip()!=old['api_key']))
        if changed:
            for job in store.all("SELECT config FROM jobs WHERE state IN ('queued','running')"):
                if json.loads(job['config']).get('profile_id')==cid:raise HTTPException(409,'此模型正被任务使用，请在任务完成后修改连接或推理参数；功能默认方案仍可独立调整')
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
        for job in store.all("SELECT config FROM jobs WHERE state IN ('queued','running')"):
            if json.loads(job['config']).get('profile_id')==cid:raise HTTPException(409,'该配置正在被分析任务使用')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');db.execute('DELETE FROM llm_configs WHERE id=?',(cid,))
            if old['is_default']:
                replacement=db.execute('SELECT id FROM llm_configs ORDER BY created,rowid LIMIT 1').fetchone()
                if replacement:db.execute('UPDATE llm_configs SET is_default=1 WHERE id=?',(replacement['id'],))
        return {'ok':True}
