"""Local-only private annotation flow. Model execution remains an explicit offline command."""
import sqlite3
from fastapi import File,Form,UploadFile,HTTPException
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field
from .quality_samples import MAX_UPLOAD

class QualityAnnotationInput(BaseModel):
    expected_version:int=Field(ge=0,le=100000)
    reference:str=Field(min_length=1,max_length=2000)
    audio_reviewed:bool

class QualityMember(BaseModel):
    id:str=Field(pattern='^[0-9a-f]{32}$')
    version:int=Field(ge=1,le=100000)

class QualityFreezeInput(BaseModel):
    members:list[QualityMember]=Field(min_length=1,max_length=100)


def register_quality_samples(app,samples):
    def call(fn,*args):
        try:return fn(*args)
        except LookupError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        except (OSError,sqlite3.Error):raise HTTPException(503,'私有样本库暂不可用，会议资料保持原样')
    @app.get('/api/runtime/quality-samples')
    def listing():return JSONResponse({'summary':call(samples.summary),'items':call(samples.listing),'datasets':call(samples.datasets),'reports':call(samples.reports)},headers={'Cache-Control':'no-store'})
    @app.post('/api/runtime/quality-samples',status_code=201)
    async def add(file:UploadFile=File(...),tags:str=Form('clean'),rights_confirmed:bool=Form(False),real_speech:bool=Form(False)):
        try:
            if not rights_confirmed or not real_speech:raise HTTPException(409,'请先确认录音使用权限和真实语音来源')
            payload=await file.read(MAX_UPLOAD+1)
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(call,samples.add,payload,[x.strip() for x in tags.split(',')],rights_confirmed,real_speech)
        finally:await file.close()
    @app.get('/api/runtime/quality-samples/{sid}')
    def get(sid:str):return JSONResponse(call(samples.get,sid),headers={'Cache-Control':'no-store'})
    @app.get('/api/runtime/quality-samples/{sid}/audio')
    def audio(sid:str):
        call(samples.get,sid)
        path=call(samples.verified_audio,sid)
        return FileResponse(path,media_type='audio/wav',headers={'Cache-Control':'no-store','Content-Disposition':'inline; filename="private-sample.wav"'})
    @app.put('/api/runtime/quality-samples/{sid}/annotation')
    def annotation(sid:str,body:QualityAnnotationInput):return call(samples.annotate,sid,body.expected_version,body.reference,body.audio_reviewed)
    @app.post('/api/runtime/quality-datasets',status_code=201)
    def freeze(body:QualityFreezeInput):return call(samples.freeze,[m.model_dump() for m in body.members])
