"""Durable ASR-to-minutes handoff, bound to the published transcript and consent."""
import hashlib,json
from types import SimpleNamespace
from pydantic import BaseModel,Field
from .speaker_labels import known_speakers
from .job_checkpoints import JobCheckpoints
from .db import encode
from .analysis import LMStudioProvider,OpenAICompatibleProvider,AIMinutesResult
from .minutes import validate_minutes_citations,MINUTES_VERSION
from .minutes_templates import MinutesTemplate
from .minutes_composition import CompositionSettings,semantic_template,build_from_ai
from .minutes_generation import MinutesGeneration
from . import meeting_flow,minutes_versions

class ProviderSelection(BaseModel):
    id:str=Field(max_length=100)
    base_url:str=Field(max_length=1000)
    provider_type:str=Field(pattern='^(local|cloud)$')
    model:str=Field(max_length=500)

class MinutesFollowupInput(BaseModel):
    profile_id:str|None=Field(default=None,max_length=100)
    model:str|None=Field(default=None,max_length=500)
    settings:CompositionSettings=Field(default_factory=CompositionSettings)
    confirm_cloud:bool=False
    expected_provider:ProviderSelection|None=None


def prepare(store,body):
    if body.settings.method!='ai':raise ValueError('自动纪要只支持根据完整文字稿生成')
    row=store.one("SELECT definition FROM minutes_templates WHERE id=? AND status='active'",(body.settings.template_id,))
    if not row:raise ValueError('纪要模板不存在或已停用')
    template=semantic_template(body.settings.template_id,MinutesTemplate.model_validate(json.loads(row['definition'])))
    profile=store.one('SELECT * FROM llm_configs WHERE id=?',(body.profile_id,)) if body.profile_id else None
    if body.profile_id and not profile:raise ValueError('纪要模型配置不存在')
    if profile:
        provider=OpenAICompatibleProvider(profile)
        model=provider.resolve_model(body.model)
        snapshot={key:profile[key] for key in ('id','name','provider_type','base_url','model')}
        if body.expected_provider and any(body.expected_provider.model_dump()[key]!=snapshot[key] for key in ('id','base_url','provider_type','model')):raise ValueError('纪要配置已变化，请重新核对模型和服务地址')
        if snapshot['provider_type']=='cloud' and (not body.confirm_cloud or not body.expected_provider):raise ValueError('使用云端自动纪要前需要本次明确确认及服务地址核对')
    else:
        provider=LMStudioProvider();model=provider.resolve_model(body.model)
        snapshot={'id':None,'name':'LM Studio','provider_type':'local','base_url':provider.base_url,'model':model}
    if body.settings.output_language is None:body.settings.output_language='zh'
    return {'profile_id':snapshot['id'],'provider':snapshot,'model':model,'settings':body.settings.model_dump(),
            'template':template.model_dump(),'cloud_confirmed':snapshot['provider_type']=='cloud' and body.confirm_cloud}


def enqueue_after_transcription(db,mid,jid,revision,plan):
    # Called in the same transaction as publishing the transcript and completing ASR.
    aid=hashlib.sha256(('auto-minutes:'+jid).encode()).hexdigest()[:32]
    config={**plan,'revision':revision,'transcription_job_id':jid}
    db.execute("INSERT OR IGNORE INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,'queued','文字稿已完成，等待整理 AI 纪要',?,'minutes')",(aid,mid,encode(config)))
    return aid


def process(worker,job):
    store=worker.store;mid=job['meeting_id'];jid=job['id'];config=json.loads(job['config'])
    def generate(_mid,_body):
        worker.check(jid)
        row=store.one('SELECT * FROM meetings WHERE id=?',(mid,))
        if not row or row['revision']!=config['revision']:raise ValueError('文字稿已变化，自动纪要暂停；请核对后重新生成')
        segments=json.loads(row['segments'])
        if not segments:raise ValueError('文字稿为空，未生成自动纪要')
        snapshot=config['provider']
        if config.get('profile_id'):
            profile=store.one('SELECT * FROM llm_configs WHERE id=?',(config['profile_id'],))
            if not profile:raise ValueError('纪要模型配置已删除，请重新选择模型')
            if any(profile[key]!=snapshot[key] for key in ('provider_type','base_url','model')):raise ValueError('纪要模型或服务地址已改变，请重新确认后生成')
            if profile['provider_type']=='cloud' and not config.get('cloud_confirmed'):raise ValueError('缺少云端自动纪要确认')
            provider=OpenAICompatibleProvider(profile)
        else:
            provider=LMStudioProvider()
            if provider.base_url!=snapshot['base_url']:raise ValueError('本地纪要服务地址已改变，请重新选择')
        settings=CompositionSettings.model_validate(config['settings']);template=MinutesTemplate.model_validate(config['template'])
        with store.connect() as db:context,context_hash=meeting_flow.ai_context(db,mid)
        if template.id=='online-interview':
            from .interview import confirmed_speaker_roles
            confirmed_speaker_roles(context,segments)
        context['composition']=settings.model_dump()
        work=store.directory(mid)/jid;work.mkdir(exist_ok=True)
        worker.update(jid,'基于完整文字稿整理 AI 纪要',.1)
        checkpoints=JobCheckpoints(store,job)
        identity=[context_hash,segments,template.model_dump(),settings.model_dump()]
        def validate_result(value):
            result=AIMinutesResult.model_validate(value)
            if not any(result.all_items()):raise ValueError('模型没有返回有效纪要内容，请检查模型后重试纪要步骤')
            document=build_from_ai(row['created'],result,settings,template=template)
            validate_minutes_citations(document,{s['id'] for s in segments})
            if document.interview:document.interview.validate_sources(segments,confirmed_speaker_roles(context,segments),require_questions=True)
            return result
        result=checkpoints.load('minutes',identity,validate_result)
        if result is None:
            speakers=known_speakers(segments)
            result=provider.generate_ai_minutes(config['model'],segments,{'title':row['title'],'date':row['created'][:10],
                'known_attendees':speakers,'minutes_template':template.model_dump(),**context},work/'result',
                lambda args,extra=None:worker.run(jid,args,work/'worker.log',extra,timeout=660))
            checkpoints.save('minutes',result.model_dump(),identity,validate_result)
        document=build_from_ai(row['created'],result,settings,template=template)
        if document.interview:
            from .interview import InterviewPreparation,project
            document.interview.validate_sources(segments)
            document.interview_context=InterviewPreparation.model_validate(context['preparation']['interview']) if context['preparation'].get('interview') else None
            from .minutes import set_output_language
            document=set_output_language(project(document),settings.output_language or 'zh')
        validate_minutes_citations(document,{s['id'] for s in segments})
        worker.check(jid);worker.update(jid,'校验原文与保存纪要草稿',.95)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()
            state=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
            if not state or state['state']!='running':
                from .worker import Cancelled
                raise Cancelled()
            if not current or current['revision']!=config['revision'] or meeting_flow.ai_context(db,mid)[1]!=context_hash:raise ValueError('生成期间文字稿或会议资料变化，请重新核对后生成')
            existing=db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes' AND json_extract(provenance,'$.job')=?",(mid,jid)).fetchone()
            aid=existing['id'] if existing else minutes_versions.publish_draft(db,mid,config['revision'],document,{
                'job':jid,'transcription_job_id':config['transcription_job_id'],'minutes_version':MINUTES_VERSION,'style':'ai',
                'ai_generated':True,'edited':False,'automatic':True,'composition':settings.model_dump(),
                'provider':snapshot['name'],'provider_type':snapshot['provider_type'],'model':config['model'],
                'template_id':template.id,'template_version':template.version,'source_transcript_revision':config['revision'],
                'flow_context_hash':context_hash,'reviewed_attention':context['reviewed_attention']})
            db.execute("UPDATE jobs SET state='completed',stage='AI 纪要草稿已保存，待人工核对',progress=1,error='' WHERE id=?",(jid,))
        return {'id':aid}
    return MinutesGeneration(store).track(generate)(mid,SimpleNamespace(job_id=jid))
