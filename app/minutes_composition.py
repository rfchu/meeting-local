"""Unified generation settings, semantic templates, and safe editorial variants."""
from typing import Literal
from pydantic import BaseModel,Field
from .minutes import MeetingMinutes,MinutesItem
from .minutes_templates import builtin_template,MinutesTemplateSection
from . import minutes_versions

class CompositionSettings(BaseModel):
    template_id:str=Field(default='general-meeting',max_length=100)
    detail_level:Literal['brief','standard','detailed']='standard'
    audience:Literal['external','internal']='external'
    method:Literal['ai','analysis','current']='ai'

class ComposeInput(CompositionSettings):
    also_analyze:bool=False
    profile_id:str|None=Field(default=None,max_length=100)
    model:str|None=Field(default=None,max_length=500)
    source_minutes_id:str|None=Field(default=None,max_length=100)


def semantic_template(template_id,definition=None):
    template=(definition or builtin_template(template_id)).model_copy(deep=True);template.version=max(2,template.version)
    if template.id=='general-meeting' and template.version==2 and not any(s.source=='summary' for s in template.sections):
        template.sections[0].title='会议概述'
        template.sections.insert(1,MinutesTemplateSection(id='discussion',title='核心内容',source='conclusions',max_items=20))
        template.sections=[section for section in template.sections if section.id!='summary']
    if not any(s.source=='summary' for s in template.sections):template.sections.append(MinutesTemplateSection(id='meeting-summary',title='会议总结',source='summary',max_items=10))
    return template


def canonical_source(document):
    value=document.model_copy(deep=True)
    if document.style=='ai' and (document.template_version or 1)<2:
        value.summary=value.conclusions;value.conclusions=value.overview;value.overview=[]
    return value


def build_from_ai(created,result,settings,source=None,template=None):
    template=template or semantic_template(settings.template_id)
    def copy(items,action=False):return [MinutesItem(**item.model_dump(),status='待开始' if action else None) for item in items]
    topic=result.basic_info.topic
    document=MeetingMinutes(title=topic if topic.endswith('会议纪要') else topic+'会议纪要',meeting_date=created[:10],
        meeting_form=result.basic_info.meeting_form,attendees='、'.join(result.basic_info.attendees),purpose=result.basic_info.purpose,
        style='ai',template_id=template.id,template_version=template.version,template_snapshot=template.model_dump(),
        basic_info_source_segment_ids=result.basic_info.source_segment_ids,overview=copy(result.overview),conclusions=copy(result.core_content),
        decisions=copy(result.decisions),action_items=copy(result.action_items,True),open_questions=copy(result.open_questions),summary=copy(result.summary))
    if template.id=='online-interview':
        if not result.interview:raise ValueError('模型未返回结构化面试结果，请使用 AI 整理全文生成')
        from .interview import project
        document.interview=result.interview.model_copy(deep=True)
        document.title=topic if topic.endswith('面试纪要') else topic+('纪要' if topic.endswith('面试') else '面试纪要')
        return project(document)
    if source:
        base=canonical_source(source)
        # Editorial variants cannot silently remove or change commitments and manual facts.
        for field in ('decisions','action_items','open_questions'):setattr(document,field,[item.model_copy(deep=True) for item in getattr(base,field)])
        for field in ('overview','conclusions','summary'):
            manual=[item.model_copy(deep=True) for item in getattr(base,field) if not item.source_segment_ids]
            setattr(document,field,[*getattr(document,field),*manual])
        for field in ('title','meeting_date','meeting_form','location','attendees','recorder','purpose','basic_info_source_segment_ids'):setattr(document,field,getattr(base,field))
    for field in ('overview','conclusions','summary'):
        if len(getattr(document,field))>50:raise ValueError('保留人工补充后条目过多，请先合并重复条目再整理')
    return document


def build_from_analysis(title,created,analysis,settings,template=None):
    template=template or semantic_template(settings.template_id)
    def copy(items,action=False):return [MinutesItem(**item.model_dump(),status='待开始' if action else None) for item in items]
    # Never truncate the first N facts to manufacture a supposedly complete brief.
    return MeetingMinutes(title=title+'会议纪要',meeting_date=created[:10],style='formal',template_id=template.id,template_version=template.version,
        template_snapshot=template.model_dump(),overview=copy(analysis.summary),conclusions=copy(analysis.key_points),
        decisions=copy(analysis.decisions),action_items=copy(analysis.action_items,True),open_questions=copy(analysis.open_questions))


def publish_analysis(store,mid,revision,analysis,analysis_id,settings,parent_id=None,template=None,job_id=None):
    document=build_from_analysis('', '',analysis,settings,template)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT title,created,revision FROM meetings WHERE id=?',(mid,)).fetchone()
        if not row or row['revision']!=revision:raise ValueError('原文版本已变化，分析结果保留，纪要未自动发布')
        if job_id:
            job=db.execute('SELECT state FROM jobs WHERE id=? AND meeting_id=?',(job_id,mid)).fetchone()
            if not job or job['state']!='running':
                from .worker import Cancelled
                raise Cancelled()
        document.title=row['title']+'会议纪要';document.meeting_date=row['created'][:10]
        existing=db.execute("SELECT id FROM artifacts WHERE meeting_id=? AND revision=? AND kind='meeting_minutes' AND json_extract(provenance,'$.followup_job')=? LIMIT 1",(mid,revision,job_id)).fetchone() if job_id else None
        aid=existing['id'] if existing else minutes_versions.publish_draft(db,mid,revision,document,{'composition':settings.model_dump(),'source_analysis_id':analysis_id,'parent_minutes_id':parent_id,'edited':False,'followup_job':job_id})
        if job_id:db.execute("UPDATE jobs SET state='completed',stage='分析与纪要草稿完成',progress=1 WHERE id=?",(job_id,))
        return aid
