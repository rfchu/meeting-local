"""Read-only readiness and stateless, explicitly fictional meeting examples."""
import os,shutil
from typing import Literal
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from urllib.parse import quote
from .providers import engine_catalog
from .minutes import MeetingMinutes,MinutesItem,minutes_markdown,minutes_docx,minutes_pdf

SEGMENTS=[
 {'id':'demo-1','start':0,'end':18,'speaker':'林悦','text':'今天确认试用版的交付范围。首版先支持录音、文字校对和纪要导出，实时翻译留到后续讨论。'},
 {'id':'demo-2','start':18,'end':38,'speaker':'陈明','text':'我负责导出样式，周五前交付预览稿。上线日期还要等测试结果，今天先不确定。'},
 {'id':'demo-3','start':38,'end':55,'speaker':'林悦','text':'好的。王宁整理测试清单，下次评审再确认上线日期。'}]

def sample_document():
    return MeetingMinutes(title='样例 · 试用版范围评审会议纪要',meeting_date='样例日期',attendees='林悦、陈明（虚构）',
        purpose='确认试用版交付范围与后续核对事项。以下均为虚构样例。',
        overview=[MinutesItem(content='讨论试用版范围、导出样式和测试安排。',source_segment_ids=['demo-1','demo-2','demo-3'])],
        decisions=[MinutesItem(content='首版支持录音、文字校对与纪要导出；实时翻译留待后续讨论。',source_segment_ids=['demo-1'])],
        action_items=[MinutesItem(content='交付导出样式预览稿。',owner='陈明',due_date='周五前（原文相对日期）',source_segment_ids=['demo-2']),
            MinutesItem(content='整理测试清单。',owner='王宁',source_segment_ids=['demo-3'])],
        open_questions=[MinutesItem(content='上线日期待测试结果与下次评审确认。',source_segment_ids=['demo-2','demo-3'])],
        summary=[MinutesItem(content='交付范围已明确，上线日期仍待确认。',source_segment_ids=['demo-1','demo-2','demo-3'])])

class ProbeInput(BaseModel):
    source:Literal['macos-system','macos-both']

def register_onboarding(app,store,native,probes):
    @app.get('/api/recording-readiness')
    def readiness():
        configs=store.all('SELECT id,name,provider_type,model FROM llm_configs ORDER BY is_default DESC,created')
        return {'native':native.capability(),'engines':engine_catalog(),
            'storage':{'writable':os.access(store.root,os.W_OK),'free_bytes':shutil.disk_usage(store.root).free},
            'capture_test_active':probes.active(),
            'llm':{'configured':bool(configs) or bool(os.getenv('LM_API_TOKEN')),'profiles':configs,'verified':False},
            'ffmpeg_ready':bool(shutil.which('ffmpeg'))}

    @app.post('/api/capture-tests',status_code=201)
    def create_probe(body:ProbeInput):
        try:return probes.start(body.source)
        except (ValueError,OSError) as error:raise HTTPException(409,str(error))
    @app.get('/api/capture-tests/{key}')
    def read_probe(key:str):
        try:return probes.read(key)
        except KeyError:raise HTTPException(404,'试录已清理，请重新测试')
    @app.get('/api/capture-tests/{key}/audio')
    def probe_audio(key:str):
        try:return Response(probes.audio(key),media_type='audio/wav',headers={'Cache-Control':'no-store'})
        except KeyError:raise HTTPException(404,'试录已清理，请重新测试')
        except ValueError as error:raise HTTPException(409,str(error))
    @app.delete('/api/capture-tests/{key}')
    def delete_probe(key:str):
        probes.delete(key);return {'ok':True}

    @app.get('/api/onboarding/sample')
    def sample():
        return {'title':'试用版范围评审（虚构样例）','segments':SEGMENTS,'minutes':sample_document().model_dump(),
            'note':{'captured_at':25,'text':'上线日期未确定，下次评审再确认。','scope':'personal'}}
    @app.get('/api/onboarding/sample/export')
    def export_sample(format:Literal['md','docx','pdf']='md'):
        document=sample_document()
        if format=='md':content=minutes_markdown(document,SEGMENTS,True).encode();media='text/markdown; charset=utf-8'
        elif format=='docx':content=minutes_docx(document,SEGMENTS,True);media='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:content=minutes_pdf(document,SEGMENTS,True);media='application/pdf'
        return Response(content,media_type=media,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote('听记-虚构样例纪要.'+format),'Cache-Control':'no-store'})
