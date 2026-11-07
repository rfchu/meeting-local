"""Durable editorial handoff; consent and source versions bind before enqueue."""
from .languages import meeting_languages
import fcntl,hashlib,json
from types import SimpleNamespace
from typing import Literal
from .db import uid,encode
from .analysis import OpenAICompatibleProvider,AIMinutesResult,MeetingAnalysis,analysis_chunk_chars,TEMPLATE_VERSION
from .auto_minutes import ProviderSelection
from .minutes import MeetingMinutes,apply_polished_minutes,validate_minutes_citations,MINUTES_VERSION
from .minutes_composition import ComposeInput,CompositionSettings,canonical_source,build_from_ai,semantic_template
from .model_preferences import resolve_profile
from .job_checkpoints import JobCheckpoints,digest
from . import minutes_versions,meeting_flow
from .configuration_queries import ConfigurationQueries
from .chinese_record import read_meeting_record

class MinutesTaskInput(ComposeInput):
    chinese_record_profile_id:str|None=None  # Empty string selects original-only coverage.
    operation:Literal['compose','polish']='compose'
    mode:Literal['quick','full']='quick'
    confirm_cloud:bool=False
    expected_provider:ProviderSelection|None=None


def provider_snapshot(profile,provider,model):
    return {'id':profile['id'] if profile else None,'name':profile['name'] if profile else 'LM Studio',
            'provider_type':profile['provider_type'] if profile else 'local','base_url':provider.base_url,'model':model,
            **({'configured_model':profile['model'],**{key:profile[key] for key in ('context_window','analysis_chunk_mode','analysis_chunk_chars','max_output_tokens') if key in profile}} if profile else {})}


class MinutesJobs:
    def __init__(self,store,meetings,providers,generation):
        self.store=store;self.meetings=meetings;self.providers=providers;self.generation=generation
    def chinese_record(self,mid,profile_id,segments):
        services=SimpleNamespace(store=self.store,configurations=ConfigurationQueries(self.store))
        record=read_meeting_record(services,mid,profile_id,0,len(segments))
        return record,{'profile_id':profile_id,'counts':record['counts'],'digest':digest(record)}
    def submit(self,mid,body):
        store=self.store;row=self.meetings.meeting(mid)
        if not row['revision'] or not json.loads(row['segments']):raise ValueError('文字稿为空，请先完成转写')
        folder=store.directory(mid);folder.mkdir(exist_ok=True)
        with (folder/'minutes-generation.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('本场纪要正在生成，请等待当前请求完成')
            settings=CompositionSettings.model_validate(body.model_dump())
            settings.output_language=body.output_language or meeting_languages(store,mid)['output_language']
            with store.connect() as db:
                context,context_hash=meeting_flow.ai_context(db,mid)
                if body.operation=='polish':source=minutes_versions.selected(db,mid,row['revision'])
                else:source=db.execute("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(body.source_minutes_id,mid)).fetchone()
                full_analysis=db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis' ORDER BY created DESC,rowid DESC LIMIT 1",(mid,row['revision'])).fetchone() if body.mode=='full' else None
                if (body.source_minutes_id or body.operation=='polish') and not source:raise ValueError('来源纪要不存在，请重新选择')
                if source and (settings.method=='current' or body.operation=='polish') and (source['revision']!=row['revision'] or not meeting_flow.artifact_current(db,mid,json.loads(source['provenance']))):raise ValueError('来源纪要已过期，请重新生成')
            if (settings.method=='current' or body.operation=='polish') and not source:raise ValueError('请先选择来源纪要')
            if source and (settings.method=='current' or body.operation=='polish') and MeetingMinutes.model_validate(json.loads(source['payload'])).interview:raise ValueError('面试纪要请核对结构化内容，或重新生成草稿')
            if body.operation=='polish':
                from .minutes_templates import MinutesTemplate
                document=MeetingMinutes.model_validate(json.loads(source['payload']))
                settings=CompositionSettings.model_validate(json.loads(source['provenance']).get('composition') or {'template_id':document.template_id,'method':'current'})
                template=MinutesTemplate.model_validate(document.template_snapshot) if document.template_snapshot else self.meetings.minutes_template(document.template_id)
            else:template=semantic_template(settings.template_id,self.meetings.minutes_template(settings.template_id))
            settings.output_language=body.output_language or (document.output_language if body.operation=='polish' else meeting_languages(store,mid)['output_language'])
            if settings.method=='current' and body.operation!='polish' and source and settings.output_language!=MeetingMinutes.model_validate(json.loads(source['payload'])).output_language:raise ValueError('转换纪要语言请使用 AI 基于全文生成')
            if template.id=='online-interview' and (body.operation!='compose' or settings.method!='ai'):raise ValueError('面试纪要仅支持 AI 整理全文')
            profile=resolve_profile(store,'minutes',body.profile_id)
            provider=OpenAICompatibleProvider(profile) if profile else self.providers.llm()
            model=provider.resolve_model(body.model);snapshot=provider_snapshot(profile,provider,model)
            if snapshot['provider_type']=='cloud':
                if not body.confirm_cloud or not body.expected_provider:raise ValueError('后台生成前需要本次确认云端模型和服务地址')
                if body.expected_provider.model_dump()!={key:snapshot[key] for key in ('id','base_url','provider_type','model')}:raise ValueError('模型或云端发送目标已变化，请重新确认')
            config={'operation':body.operation,'revision':row['revision'],'title':row['title'],'created':row['created'],'context_hash':context_hash,
                'settings':settings.model_dump(),'template':template.model_dump(),'profile_id':snapshot['id'],'provider':snapshot,'model':model,
                'source_minutes_id':source['id'] if source else None,'source_analysis_id':full_analysis['id'] if full_analysis else None,'cloud_confirmed':snapshot['provider_type']=='cloud' and body.confirm_cloud,
                'also_analyze':body.also_analyze and body.operation=='compose' and settings.method=='ai' and template.id!='online-interview','mode':body.mode}
            if body.chinese_record_profile_id is not None:
                if body.operation!='compose' or settings.method!='ai':raise ValueError('中文会议记录只用于 AI 整理全文')
                record,identity=self.chinese_record(mid,body.chinese_record_profile_id,json.loads(row['segments']))
                if record['revision']!=row['revision']:raise ValueError('文字稿版本已变化，请刷新后再试')
                config['chinese_record']=identity
            config['request_key']=digest(config)
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT revision,title FROM meetings WHERE id=?',(mid,)).fetchone()
                if not current or current['revision']!=row['revision'] or current['title']!=row['title'] or meeting_flow.ai_context(db,mid)[1]!=context_hash:raise ValueError('资料已变化，请刷新后重试')
                pending=db.execute("SELECT id,config FROM jobs WHERE meeting_id=? AND state IN ('queued','running') LIMIT 1",(mid,)).fetchone()
                if pending:
                    if json.loads(pending['config']).get('request_key')==config['request_key']:return {'job_id':pending['id'],'reused':True,'revision':row['revision']}
                    raise ValueError('本场已有任务等待或执行，请先完成或取消')
                if body.operation=='compose' and body.mode=='full' and template.id!='online-interview' and not db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis'",(mid,row['revision'])).fetchone():raise ValueError('完整模式请先生成当前文字稿的分析')
                if body.operation=='compose' and settings.method=='analysis':raise ValueError('根据已有分析生成请使用规则纪要入口')
                jid=uid();db.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,?,'minutes','queued','等待后台整理纪要',?)",(jid,mid,encode(config)))
            return {'job_id':jid,'reused':False,'revision':row['revision']}
    def process(self,worker,job):
        return self.generation.track(lambda mid,body:self._process(worker,job))(job['meeting_id'],SimpleNamespace(job_id=job['id']))
    def _process(self,worker,job):
        store=self.store;mid=job['meeting_id'];jid=job['id'];config=json.loads(job['config']);snapshot=config['provider']
        row=self.meetings.meeting(mid)
        with store.connect() as db:context,context_hash=meeting_flow.ai_context(db,mid)
        if row['revision']!=config['revision'] or row['title']!=config['title'] or context_hash!=config['context_hash']:raise ValueError('原文或会议资料已变化，请重新核对后生成')
        profile=store.one('SELECT * FROM llm_configs WHERE id=?',(config['profile_id'],)) if config['profile_id'] else None
        if config['profile_id'] and not profile:raise ValueError('模型配置已删除，请重新选择')
        provider=OpenAICompatibleProvider(profile) if profile else self.providers.llm()
        actual=provider_snapshot(profile,provider,config['model'])
        if any(actual.get(key)!=value for key,value in snapshot.items() if key!='name'):raise ValueError('模型配置或服务地址已变化，请重新确认')
        if snapshot['provider_type']=='cloud' and not config['cloud_confirmed']:raise ValueError('缺少本次云端发送确认')
        settings=CompositionSettings.model_validate(config['settings'])
        provider.output_language=settings.output_language or 'zh'
        from .minutes_templates import MinutesTemplate
        template=MinutesTemplate.model_validate(config['template'])
        segments=json.loads(row['segments']);source=None;source_document=None
        if config['source_minutes_id'] and (settings.method=='current' or config['operation']=='polish'):
            source=store.one("SELECT * FROM artifacts WHERE id=? AND meeting_id=? AND kind='meeting_minutes'",(config['source_minutes_id'],mid))
            with store.connect() as db:
                if not source or source['revision']!=row['revision'] or not meeting_flow.artifact_current(db,mid,json.loads(source['provenance'])):raise ValueError('来源纪要已变化，请重新选择')
                selected=minutes_versions.selected(db,mid,row['revision'])
                if config['operation']=='polish' and (not selected or selected['id']!=source['id']):raise ValueError('采用的纪要已变化，请重新选择')
            source_document=MeetingMinutes.model_validate(json.loads(source['payload']))
        if (identity:=config.get('chinese_record')) is not None:
            record,current_identity=self.chinese_record(mid,identity['profile_id'],segments)
            if current_identity!=identity:raise ValueError('中文会议记录已变化，请重新生成')
            translations={item['id']:item['text'] for item in record['items'] if item['status']=='translated'}
            segments=[{**item,**({'translation_zh':translations[item['id']]} if item['id'] in translations else {})} for item in segments]
            context['chinese_record_coverage']=identity['counts']
        context['composition']=settings.model_dump()
        if settings.method=='current' and config['operation']=='compose':
            canonical=canonical_source(source_document);payload=canonical.model_dump()
            for field in ('overview','conclusions','decisions','action_items','open_questions','summary'):payload[field]=[item for item in payload[field] if item['source_segment_ids']]
            context['source_minutes']=payload
            ids={sid for item in canonical.all_items() for sid in item.source_segment_ids}|set(canonical.basic_info_source_segment_ids)
            segments=[item for item in segments if item['id'] in ids]
            if not segments:raise ValueError('此纪要没有原文依据，不能用 AI 派生')
        work=store.directory(mid)/jid;checkpoints=JobCheckpoints(store,job);work.mkdir(exist_ok=True)
        run=lambda args,extra=None:worker.run(jid,args,work/'worker.log',extra,timeout=660)
        inputs=[context,segments,json.loads(source['payload']) if source else None]
        def validate(value):
            if config['operation']=='polish':
                result=MeetingAnalysis.model_validate(value)
                valid={sid for item in source_document.all_items() for sid in item.source_segment_ids}
                from .analysis import validate_citations
                validate_citations(result,valid)
                from .minutes import set_output_language
                document=set_output_language(apply_polished_minutes(source_document,result),settings.output_language or 'zh')
            else:
                result=AIMinutesResult.model_validate(value)
                if not any(result.all_items()):raise ValueError('模型未返回有效纪要')
                document=build_from_ai(row['created'],result,settings,source_document if settings.method=='current' else None,template)
                if document.interview:
                    from .interview import InterviewPreparation,project
                    document.interview.validate_sources(segments)
                    document.interview_context=InterviewPreparation.model_validate(context['preparation']['interview']) if context['preparation'].get('interview') else None
                    from .minutes import set_output_language
                    document=set_output_language(project(document),settings.output_language or 'zh')
            validate_minutes_citations(document,{item['id'] for item in segments})
            return document
        worker.update(jid,'后台整理纪要，完成后自动保存草稿',.1)
        document=checkpoints.load('editorial-result',inputs,validate)
        if document is None:
            worker.boundary(jid)
            if config['operation']=='polish':result=provider.polish_minutes(config['model'],source_document,work/'result',run)
            else:result=provider.generate_ai_minutes(config['model'],segments,{'title':row['title'],'date':row['created'][:10],
                'known_attendees':list(dict.fromkeys(item['speaker'] for item in segments if item.get('speaker') and item['speaker']!='未分配')),'minutes_template':template.model_dump(),**context},work/'result',run)
            document=checkpoints.save('editorial-result',result.model_dump(),inputs,validate)
        worker.check(jid)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE');worker.check(jid)
            current=db.execute('SELECT revision,title FROM meetings WHERE id=?',(mid,)).fetchone()
            if not current or current['revision']!=config['revision'] or current['title']!=config['title'] or meeting_flow.ai_context(db,mid)[1]!=context_hash:raise ValueError('生成期间资料已变化，草稿未发布')
            if config['operation']=='polish':
                selected=minutes_versions.selected(db,mid,row['revision'])
                if not selected or selected['id']!=source['id']:raise ValueError('润色期间采用稿已变化')
            existing=db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes' AND json_extract(provenance,'$.job')=?",(mid,jid)).fetchone()
            provenance={'job':jid,'minutes_version':MINUTES_VERSION,'style':document.style,'template_id':document.template_id,'template_version':document.template_version,
                'edited':False,'ai_generated':True,'provider':snapshot['name'],'provider_type':snapshot['provider_type'],'model':config['model'],
                'source_transcript_revision':config['revision'],'flow_context_hash':context_hash,'reviewed_attention':context['reviewed_attention'],'reviewed_live_overview':context.get('reviewed_live_overview',[]),'composition':settings.model_dump(),
                'source_analysis_id':config['source_analysis_id'],'parent_minutes_id':config['source_minutes_id'],'source_minutes_dependencies':[source['id']] if source else []}
            if identity is not None:
                provenance['chinese_record']=identity
                cited=set(document.basic_info_source_segment_ids)
                cited.update(sid for item in document.all_items() for sid in item.source_segment_ids)
                if document.interview:cited.update(e.segment_id for e in document.interview.evidence())
                provenance['citation_translations']={sid:translations[sid] for sid in sorted(cited & translations.keys())}
            if config['operation']=='polish':
                provenance['llm_polished']=True
                original=json.loads(source['provenance'])
                adopted=[item for item in original.get('adopted_notes',[]) if any(entry.content==item['content'] and entry.source_segment_ids==item['source_segment_ids'] for entry in getattr(document,item['section']))]
                if adopted:provenance['adopted_notes']=adopted
            aid=existing['id'] if existing else minutes_versions.publish_draft(db,mid,config['revision'],document,provenance)
            db.execute("UPDATE jobs SET state='completed',stage='纪要草稿已保存，待核对',progress=1,error='' WHERE id=? AND state='running'",(jid,))
            if config['also_analyze'] and not db.execute("SELECT 1 FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_analysis'",(mid,config['revision'])).fetchone():
                analysis_id=hashlib.sha256(('minutes-analysis:'+jid).encode()).hexdigest()[:32]
                window=snapshot.get('context_window',32768);mode=snapshot.get('analysis_chunk_mode','auto')
                plan={'revision':config['revision'],'model':config['model'],'profile_id':config['profile_id'],'provider':snapshot,'context_window':window,'template_version':TEMPLATE_VERSION,
                    'max_chars':analysis_chunk_chars(window,mode,snapshot.get('analysis_chunk_chars',0))}
                db.execute("INSERT OR IGNORE INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,?,'analysis','queued','纪要完成，等待独立分析',?)",(analysis_id,mid,encode(plan)))
        return {'id':aid}
