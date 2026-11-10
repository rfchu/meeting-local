"""Minutes HTTP routes; shared services are supplied by the composition root."""
from ..analysis_jobs import require_cloud_confirmation
from typing import Literal
from ..speaker_labels import known_speakers
from ..minutes_templates import AudienceTemplateUpdate, audience_template, save_audience_template
from ..languages import meeting_languages
from ..minutes import set_output_language
from ..resources import optional_slot
from ..minutes_jobs import MinutesTaskInput
from ..model_preferences import resolve_profile
from ..interview import InterviewPreparation
import json, os, subprocess
from urllib.parse import quote
from fastapi import HTTPException,Request
from fastapi.responses import Response,JSONResponse
from ..db import uid, encode
from ..analysis import OpenAICompatibleProvider, MeetingAnalysis
from ..minutes import MeetingMinutes, build_minutes, build_ai_minutes, apply_polished_minutes, validate_minutes_citations, minutes_markdown, minutes_docx, minutes_pdf, MINUTES_VERSION
from ..minutes_composition import ComposeInput, CompositionSettings, build_from_ai, publish_analysis, canonical_source
from .. import minutes_versions,minutes_delivery
from .. import meeting_flow
from ..http_schemas import DeliverySentInput, AIMinutesGenerateInput, AnalysisJobInput, MinutesGenerateInput, MinutesPolishInput, MinutesSaveInput
from ..application_services import ApplicationServices

def register_routes(app,services:ApplicationServices):
    enqueue_analysis=services.analysis_jobs.submit
    meeting=services.meetings.meeting
    minutes_template=services.meetings.minutes_template
    public_artifact=services.meetings.public_artifact
    minutes_generation=services.minutes_generation
    store=services.store
    LMStudioProvider=services.providers.llm

    def minutes_source(mid,row,analysis_id=None):
        if analysis_id:
            artifact=store.one("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND revision=? AND kind='meeting_analysis'",(analysis_id,mid,row['revision']))
        else:
            artifact=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
        if not artifact:raise HTTPException(409,'请先为当前文字稿生成会议分析')
        return artifact


    @app.get('/api/audience-templates/{audience}')
    def read_audience_template(audience:Literal['internal','external']):
        with store.connect() as db:
            return audience_template(db,audience)

    @app.put('/api/audience-templates/{audience}')
    def update_audience_template(audience:Literal['internal','external'],body:AudienceTemplateUpdate):
        try:return save_audience_template(store,audience,body)
        except ValueError as error:raise HTTPException(409,str(error))

    @app.post('/api/meetings/{mid}/minutes',status_code=201)
    def generate_minutes(mid:str,body:MinutesGenerateInput):
        row=meeting(mid);source=minutes_source(mid,row)
        segments=json.loads(row['segments'])
        analysis=MeetingAnalysis.model_validate(json.loads(source['payload']))
        template=minutes_template(body.template_id)
        if template.id=='online-interview':raise HTTPException(409,'面试模板请使用 AI 整理全文')
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
        try:
            if body.document.template_id=='online-interview':
                from ..interview import project
                if not body.document.interview:raise ValueError('面试纪要缺少结构化结果')
                roles=body.document.interview_context.speaker_roles if body.document.interview_context else None
                if roles and roles.revision!=row['revision']:
                    raise ValueError('发言人确认所用的文字稿已变化，请重新确认并生成面试纪要')
                body.document.interview.validate_sources(segments,roles.segment_roles(segments) if roles else None,raise_on_error=True)
                body.document=project(body.document)
            validate_minutes_citations(body.document,valid_ids)
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
                if not base or (not body.allow_branch and (not latest or latest['id']!=body.source_minutes_id)):raise HTTPException(409,'纪要版本已变化，请刷新后再保存')
                original=json.loads(base['provenance'])
                for key in ('flow_context_hash','reviewed_attention','reviewed_live_overview','composition','source_minutes_dependencies','chinese_record','citation_translations'):
                    if key in original:provenance[key]=original[key]
                adopted=[n for n in original.get('adopted_notes',[]) if any(item.content==n['content'] and item.source_segment_ids==n['source_segment_ids'] for item in getattr(body.document,n['section']))]
                if adopted:provenance['adopted_notes']=adopted
            if body.merge_from_minutes_id:
                adopted=minutes_versions.selected(db,mid)
                other=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND revision=? AND kind='meeting_minutes'",
                                 (body.merge_from_minutes_id,mid,row['revision'])).fetchone()
                if (not adopted or adopted['id']!=body.source_minutes_id or adopted['revision']!=row['revision']
                    or not other or other['id']==adopted['id']
                    or any(not meeting_flow.artifact_current(db,mid,json.loads(a['provenance'])) for a in (adopted,other))):
                    raise HTTPException(409,'采用稿或来源已变化，请重新比较')
                minutes_versions.adopt(db,mid,adopted['id'])
                provenance['source_minutes_dependencies']=[adopted['id'],other['id']]
                adopted_notes=[*provenance.get('adopted_notes',[]),
                    *json.loads(other['provenance']).get('adopted_notes',[])]
                matched={item['note_id']:item for item in adopted_notes
                    if any(row.content==item['content'] and row.source_segment_ids==item['source_segment_ids']
                           for row in getattr(body.document,item['section']))}
                if matched:provenance['adopted_notes']=list(matched.values())
            if body.source_minutes_id:
                provenance['parent_minutes_id']=body.source_minutes_id
                adopted=minutes_versions.selected(db,mid)
                provenance['draft']=bool(body.merge_from_minutes_id or (adopted and adopted['id']!=body.source_minutes_id))
            db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (aid,mid,row['revision'],'meeting_minutes',encode(body.document.model_dump()),encode(provenance)))
            if body.source_minutes_id and not body.merge_from_minutes_id:minutes_versions.follow_edit(db,mid,body.source_minutes_id,aid)
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))


    @app.post('/api/meetings/{mid}/minutes/compose',status_code=201)
    def compose_minutes(mid:str,body:ComposeInput,request:Request):
        if 'respond-async' in request.headers.get('Prefer','').lower():
            return submit_minutes_task(mid,MinutesTaskInput(**body.model_dump()))
        row=meeting(mid);settings=CompositionSettings.model_validate(body.model_dump())
        from ..minutes_composition import semantic_template
        template=semantic_template(settings.template_id,minutes_template(settings.template_id))
        if template.id=='online-interview' and settings.method!='ai':raise HTTPException(409,'面试模板请使用 AI 整理全文，问答与证据不能由普通会议分析代替')
        if body.source_minutes_id and not store.one("SELECT id FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(body.source_minutes_id,mid)):raise HTTPException(404,'来源纪要不存在')
        if settings.method=='analysis':
            if settings.output_language=='en':raise HTTPException(409,'规则纪要沿用分析原文，转换输出语言请使用 AI 整理')
            analysis=store.one("SELECT * FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision']))
            if body.expected_analysis_id and (not analysis or analysis['id']!=body.expected_analysis_id):
                raise HTTPException(409,'当前文字稿的分析已变化，请返回深入分析核对后再成稿')
            if not analysis:
                plan=enqueue_analysis(mid,AnalysisJobInput(profile_id=body.profile_id,model=body.model,confirm_cloud=body.confirm_cloud,expected_provider=body.expected_provider,minutes_followup={'settings':settings.model_dump(),'parent_minutes_id':body.source_minutes_id,'template':template.model_dump()}))
                return {'job_id':plan['id']}
            try:aid=publish_analysis(store,mid,row['revision'],MeetingAnalysis.model_validate(json.loads(analysis['payload'])),analysis['id'],settings,body.source_minutes_id,template=template)
            except ValueError as error:raise HTTPException(409,str(error))
            return {'artifact':public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))}
        if settings.method=='current' and not body.source_minutes_id:raise HTTPException(409,'请先选择要整理的纪要版本')
        result=generate_ai_minutes_sync(mid,AIMinutesGenerateInput(profile_id=body.profile_id,model=body.model,confirm_cloud=body.confirm_cloud,expected_provider=body.expected_provider,composition=settings,source_minutes_id=body.source_minutes_id))
        response={'artifact':result}
        if body.also_analyze and settings.method=='ai' and template.id!='online-interview' and not store.one("SELECT id FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis'",(mid,result['revision'])):
            try:
                if meeting(mid)['revision']!=result['revision']:raise HTTPException(409,'原文版本已变化')
                response['analysis_job_id']=enqueue_analysis(mid,AnalysisJobInput(profile_id=body.profile_id,model=body.model,confirm_cloud=body.confirm_cloud,expected_provider=body.expected_provider))['id']
            except HTTPException as error:response['warning']='纪要已保存；额外分析未启动：'+str(error.detail)
        return response


    @app.post('/api/meetings/{mid}/minutes/{aid}/adopt')
    def adopt_minutes(mid:str,aid:str):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            artifact=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(aid,mid)).fetchone()
            if not row or not artifact:raise HTTPException(404,'纪要不存在')
            if artifact['revision']!=row['revision'] or not meeting_flow.artifact_current(db,mid,json.loads(artifact['provenance'])):raise HTTPException(409,'此版本来源已过期，请根据当前原文重新生成')
            minutes_versions.adopt(db,mid,aid)
        return {'id':aid}


    @app.post('/api/meetings/{mid}/minutes/jobs',status_code=202)
    def submit_minutes_task(mid:str,body:MinutesTaskInput):
        try:result=services.minutes_jobs.submit(mid,body)
        except (ValueError,RuntimeError) as error:raise HTTPException(409,str(error))
        return JSONResponse(result,status_code=202,headers={'Location':'/api/runtime/tasks','Cache-Control':'no-store'})

    @app.post('/api/meetings/{mid}/minutes/ai',status_code=201)
    def generate_ai_minutes(mid:str,body:AIMinutesGenerateInput,request:Request):
        if 'respond-async' in request.headers.get('Prefer','').lower():
            settings=body.composition.model_dump() if body.composition else {'template_id':body.template_id}
            settings['output_language']=body.output_language or settings.get('output_language')
            return submit_minutes_task(mid,MinutesTaskInput(**settings,profile_id=body.profile_id,model=body.model,source_minutes_id=body.source_minutes_id,mode=body.mode))
        return generate_ai_minutes_sync(mid,body)

    @minutes_generation.track
    def generate_ai_minutes_sync(mid:str,body:AIMinutesGenerateInput):
        row=meeting(mid);segments=json.loads(row['segments'])
        output_language=body.output_language or (body.composition.output_language if body.composition else None) or meeting_languages(store,mid)['output_language']
        if body.composition:body.composition.output_language=output_language
        if not segments:raise HTTPException(409,'文字稿为空，无法生成 AI 会议纪要')
        template=minutes_template(body.composition.template_id if body.composition else body.template_id)
        if body.composition:
            from ..minutes_composition import semantic_template
            template=semantic_template(template.id,template)
        if template.id=='online-interview' and body.composition and body.composition.method!='ai':raise HTTPException(409,'面试模板仅支持 AI 整理全文')
        source_analysis=minutes_source(mid,row) if body.mode=='full' and template.id!='online-interview' else None
        try:
            with store.connect() as db:context,context_hash=meeting_flow.ai_context(db,mid)
        except ValueError as error:raise HTTPException(409,str(error))
        source_document=None;source_ids=None
        if body.composition:
            context['composition']=body.composition.model_dump()
            if body.composition.method=='current':
                source=store.one("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(body.source_minutes_id,mid))
                with store.connect() as db:
                    if not source or source['revision']!=row['revision'] or not meeting_flow.artifact_current(db,mid,json.loads(source['provenance'])):raise HTTPException(409,'来源纪要已过期，请选择当前有效版本')
                source_document=MeetingMinutes.model_validate(json.loads(source['payload']))
                if source_document.interview:raise HTTPException(409,'面试纪要请重新生成面试草稿，或直接核对结构化内容；不使用通用纪要派生')
                if source_document.output_language!=output_language:raise HTTPException(409,'转换纪要语言请使用 AI 基于全文生成')
                canonical=canonical_source(source_document)
                # Keep unreferenced manual additions outside the rewrite model and reattach verbatim.
                source_payload=canonical.model_dump()
                for section in ('overview','conclusions','decisions','action_items','open_questions','summary'):
                    source_payload[section]=[item for item in source_payload[section] if item['source_segment_ids']]
                context['source_minutes']=source_payload
                source_ids={sid for item in canonical.all_items() for sid in item.source_segment_ids}|set(canonical.basic_info_source_segment_ids)
                segments=[segment for segment in segments if segment['id'] in source_ids]
                if not segments:raise HTTPException(409,'此纪要没有原文依据，不能用 AI 派生；可继续人工编辑或从全文重新生成')
        profile=resolve_profile(store,'minutes',body.profile_id)
        try:
            if profile:
                provider=OpenAICompatibleProvider(profile);model=provider.resolve_model(body.model)
                require_cloud_confirmation(profile,body,model)
                provider_name=profile['name'];provider_type=profile['provider_type']
            else:
                provider=LMStudioProvider();model=provider.resolve_model(body.model)
                provider_name='LM Studio';provider_type='local'
            work=store.directory(mid)/('ai-minutes-'+uid());work.mkdir(exist_ok=True)
            def run(args,extra_env=None):
                env=os.environ.copy()
                if extra_env:env.update(extra_env)
                try:completed=subprocess.run(args,capture_output=True,env=env,timeout=660)
                except subprocess.TimeoutExpired:raise RuntimeError('AI 纪要生成超过 11 分钟，模型未返回完整结果；请检查模型思考模式或缩小输入后重试')
                if completed.returncode:
                    detail=(completed.stderr or completed.stdout).decode(errors='replace')[-1800:]
                    raise RuntimeError(f'AI 会议纪要进程失败（{completed.returncode}）：{detail}')
            speakers=known_speakers(segments)
            resource='cloud_llm' if provider_type=='cloud' else 'local_llm'
            with optional_slot(services.resources,'minutes',resource,('inline_minutes',mid),legacy=resource=='local_llm'):
                result=provider.generate_ai_minutes(model,segments,{'title':row['title'],'date':row['created'][:10],
                    'known_attendees':speakers,'minutes_template':template.model_dump(),**context,'output_language':output_language},work/'result',run)
            document=build_from_ai(row['created'],result,body.composition,source_document,template) if body.composition else build_ai_minutes(row['created'],result,template)
            document=set_output_language(document,output_language)
            if template.id=='online-interview':
                from ..interview import confirmed_speaker_roles
                document.interview.validate_sources(segments,confirmed_speaker_roles(context,segments),require_questions=True,retain_pending=True,raise_on_error=False)
                document.interview_context=InterviewPreparation.model_validate(context['preparation']['interview']) if context['preparation'].get('interview') else None
                from ..interview import project
                document=project(document)
            document=set_output_language(document,output_language)
            validate_minutes_citations(document,{segment['id'] for segment in segments})
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:raise HTTPException(409,str(error))
        aid=uid();provenance={'minutes_version':MINUTES_VERSION,'style':'ai','template_id':template.id,
            'template_version':template.version,'edited':False,'ai_generated':True,
            'flow_context_hash':context_hash,'reviewed_attention':context['reviewed_attention'],'reviewed_live_overview':context.get('reviewed_live_overview',[]),
            'generation_mode':body.mode,'source_transcript_revision':row['revision'],'provider':provider_name,'provider_type':provider_type,'model':model}
        if source_analysis:provenance['source_analysis_id']=source_analysis['id']
        if body.composition:provenance.update(composition=body.composition.model_dump(),parent_minutes_id=body.source_minutes_id)
        if source_document:provenance['source_minutes_dependencies']=[body.source_minutes_id]
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            try:current_context_hash=meeting_flow.ai_context(db,mid)[1]
            except ValueError as error:raise HTTPException(409,str(error))
            if not current or current['revision']!=row['revision'] or current_context_hash!=context_hash:raise HTTPException(409,'生成期间原文或会议准备／关注点变化，请重新生成')
            if body.composition or template.id=='online-interview':aid=minutes_versions.publish_draft(db,mid,row['revision'],document,provenance)
            else:db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (aid,mid,row['revision'],'meeting_minutes',encode(document.model_dump()),encode(provenance)))
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))


    @app.post('/api/meetings/{mid}/minutes/polish',status_code=201)
    def polish_minutes(mid:str,body:MinutesPolishInput,request:Request):
        if 'respond-async' in request.headers.get('Prefer','').lower():
            return submit_minutes_task(mid,MinutesTaskInput(operation='polish',profile_id=body.profile_id,model=body.model,output_language=body.output_language))
        return polish_minutes_sync(mid,body)

    @minutes_generation.track
    def polish_minutes_sync(mid:str,body:MinutesPolishInput):
        row=meeting(mid)
        with store.connect() as db:source=minutes_versions.selected(db,mid,row['revision'])
        if not source:raise HTTPException(409,'请先生成会议纪要')
        with store.connect() as db:
            if not meeting_flow.artifact_current(db,mid,json.loads(source['provenance'] or '{}')):raise HTTPException(409,'纪要来源已变化，请重新生成后再润色')
            try:polish_context_hash=meeting_flow.ai_context(db,mid)[1]
            except ValueError as error:raise HTTPException(409,str(error))
        document=MeetingMinutes.model_validate(json.loads(source['payload']))
        output_language=body.output_language or document.output_language
        if document.template_id=='online-interview':raise HTTPException(409,'面试纪要请直接核对结构化内容，或重新生成草稿')
        segments=json.loads(row['segments']);valid_ids={segment['id'] for segment in segments}
        try:validate_minutes_citations(document,valid_ids)
        except ValueError as error:raise HTTPException(400,str(error))
        profile=resolve_profile(store,'minutes',body.profile_id)
        try:
            if profile:
                provider=OpenAICompatibleProvider(profile);model=provider.resolve_model(body.model)
                require_cloud_confirmation(profile,body,model)
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
            resource='cloud_llm' if provider_type=='cloud' else 'local_llm'
            with optional_slot(services.resources,'minutes',resource,('inline_minutes',mid),legacy=resource=='local_llm'):
                provider.output_language=output_language
                result=provider.polish_minutes(model,document,work/'result',run)
            polished=set_output_language(apply_polished_minutes(document,result),output_language)
            validate_minutes_citations(polished,valid_ids)
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:raise HTTPException(409,str(error))
        source_provenance=json.loads(source['provenance'] or '{}')
        aid=uid();provenance={'source_analysis_id':source_provenance.get('source_analysis_id'),'source_minutes_id':source['id'],
            'minutes_version':MINUTES_VERSION,'style':polished.style,'template_id':polished.template_id,
            'template_version':polished.template_version,'edited':False,'llm_polished':True,
            'provider':provider_name,'provider_type':provider_type,'model':model}
        for key in ('flow_context_hash','reviewed_attention','reviewed_live_overview','composition','source_minutes_dependencies'):
            if key in source_provenance:provenance[key]=source_provenance[key]
        adopted=[n for n in source_provenance.get('adopted_notes',[]) if any(item.content==n['content'] and item.source_segment_ids==n['source_segment_ids'] for item in getattr(polished,n['section']))]
        if adopted:provenance['adopted_notes']=adopted
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest=minutes_versions.selected(db,mid,row['revision'])
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            try:current_context_hash=meeting_flow.ai_context(db,mid)[1]
            except ValueError as error:raise HTTPException(409,str(error))
            if not current or current['revision']!=row['revision'] or not latest or latest['id']!=source['id'] or current_context_hash!=polish_context_hash:raise HTTPException(409,'润色期间来源已变化，请重新核对')
            db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                (aid,mid,row['revision'],'meeting_minutes',encode(polished.model_dump()),encode(provenance)))
            minutes_versions.follow_edit(db,mid,source['id'],aid)
        return public_artifact(store.one('SELECT * FROM artifacts WHERE id=?',(aid,)))


    @app.put('/api/meetings/{mid}/minutes/deliveries/{delivery_id}/sent')
    def set_delivery_sent(mid:str,delivery_id:str,body:DeliverySentInput):
        try:return minutes_delivery.set_sent(store,mid,delivery_id,body.sent)
        except KeyError:raise HTTPException(404,'交付版本不存在')

    @app.get('/api/meetings/{mid}/minutes/export/{format}')
    def export_minutes(mid:str,format:str,include_citations:bool=False,inline:bool=False,artifact_id:str|None=None,delivery_id:str|None=None):
        if format not in ('md','docx','pdf'):raise HTTPException(400,'会议纪要仅支持 Markdown、Word 或 PDF')
        row=meeting(mid)
        citation_appendix=False
        if delivery_id:
            delivery=store.one('SELECT payload FROM minutes_deliveries WHERE id=? AND meeting_id=?',(delivery_id,mid))
            if not delivery:raise HTTPException(404,'交付版本不存在')
            frozen=json.loads(delivery['payload']);document=MeetingMinutes.model_validate(frozen['document'])
            segments=frozen['segments'];include_citations=frozen['include_citations']
            citation_appendix='audience' in frozen
        else:
            with store.connect() as db:
                artifact=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(artifact_id,mid)).fetchone() if artifact_id else minutes_versions.selected(db,mid,row['revision'])
            if not artifact:raise HTTPException(404,'当前文字稿版本还没有会议纪要')
            document=MeetingMinutes.model_validate(json.loads(artifact['payload']));source=store.one('SELECT segments FROM revisions WHERE meeting_id=? AND revision=?',(mid,artifact['revision']))
            segments=json.loads(source['segments']) if source else []
        validate_minutes_citations(document,{segment['id'] for segment in segments})
        if format=='md':
            content=minutes_markdown(document,segments,include_citations,citation_appendix=citation_appendix)
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
