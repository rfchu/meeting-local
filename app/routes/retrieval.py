"""Retrieval HTTP routes; shared services are supplied by the composition root."""
import sqlite3
from pathlib import Path
from fastapi import File, UploadFile, HTTPException
from ..db import uid, encode
from ..http_schemas import IndexBuildInput, QAGenerateInput, QAPrepareInput, RetrievalConfigInput, SearchInput, SearchResolveInput
from ..application_services import ApplicationServices

def register_routes(app,services:ApplicationServices):
    local_embedding=services.providers.embedding
    live_asr_lock=services.live_asr_lock
    qa=services.qa
    retrieval=services.retrieval
    store=services.store
    worker=services.worker

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
        if store.one("SELECT 1 FROM live_sessions WHERE status IN ('running','paused','draining') LIMIT 1"):raise HTTPException(409,'实时识别期间不切换向量模型，请在录音结束后测试并保存')
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
