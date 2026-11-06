"""Model configuration lookup and validation shared by HTTP adapters."""
from fastapi import HTTPException
from .analysis import validate_llm_endpoint, analysis_chunk_chars

class ConfigurationQueries:
    def __init__(self,store):self.store=store
    def get(self,cid):
        store=self.store
        row=store.one('SELECT * FROM llm_configs WHERE id=?',(cid,))
        if not row:raise HTTPException(404,'模型配置不存在')
        return row

    def checked(self,body):
        try:base_url=validate_llm_endpoint(body.provider_type,body.base_url)
        except ValueError as error:raise HTTPException(400,str(error))
        api_key=(body.api_key or '').strip()
        if body.provider_type=='cloud' and not api_key:raise HTTPException(400,'云端模型需要 API Key')
        try:analysis_chunk_chars(body.context_window,body.analysis_chunk_mode,body.analysis_chunk_chars)
        except ValueError as error:raise HTTPException(400,str(error))
        return base_url,api_key,body.context_window
