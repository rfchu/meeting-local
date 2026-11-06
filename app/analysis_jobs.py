"""Analysis job submission shared by meeting and minutes workflows."""
from .model_preferences import resolve_profile
from fastapi import HTTPException
from .db import uid, encode
from .analysis import OpenAICompatibleProvider, analysis_chunk_chars, TEMPLATE_VERSION
from .http_schemas import AnalysisJobInput

class AnalysisJobs:
    def __init__(self,store,meetings,providers):self.store=store;self.meetings=meetings;self.providers=providers
    def submit(self,mid:str,body:AnalysisJobInput):
        store=self.store;meeting=self.meetings.meeting;LMStudioProvider=self.providers.llm
        row=meeting(mid)
        if not row['revision']:raise HTTPException(409,'请先完成并校对文字稿')
        profile=resolve_profile(store,'analysis',body.profile_id)
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
            if body.minutes_followup:config['minutes_followup']=body.minutes_followup
            db.execute('INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)',
                (jid,mid,'queued','等待分析并生成纪要' if body.minutes_followup else '等待分析',encode(config),'analysis'))
        return {'id':jid,'model':model,'revision':row['revision']}
