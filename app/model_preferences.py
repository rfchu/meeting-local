"""Workspace-owned model routing. Defaults never start jobs or grant cloud consent."""
from fastapi import HTTPException
from pydantic import BaseModel,Field

LLM_ROLES=('analysis','minutes','overview','notes','speakers','qa','terminology','correction')
ENGINE_ROLES=('transcription','live_transcription')
ROLES=LLM_ROLES+ENGINE_ROLES
class ChoiceInput(BaseModel):
    value:str=Field(default='',max_length=100)
    version:int|None=Field(default=None,ge=0)
class ImportInput(BaseModel):
    choices:dict[str,str]=Field(default_factory=dict,max_length=20)

def valid(db,role,value):
    return value in ('whisper','qwen') if role in ENGINE_ROLES else bool(db.execute('SELECT 1 FROM llm_configs WHERE id=?',(value,)).fetchone())

def snapshot(db):
    profiles=[dict(r) for r in db.execute('SELECT id,name,model,provider_type,is_default FROM llm_configs ORDER BY is_default DESC,created,rowid')]
    local=next((p['id'] for p in profiles if p['provider_type']=='local'),'')
    rows={r['role']:dict(r) for r in db.execute('SELECT * FROM model_preferences')};result={}
    for role in ROLES:
        row=rows.get(role,{});last=row.get('last_value','');default=row.get('default_value','')
        fallback='whisper' if role in ENGINE_ROLES else local
        options=[(last,'last'),(default,'default'),(fallback,'local_fallback')]
        selected=next(((value,source) for value,source in options if value and valid(db,role,value)),('','unset'))
        result[role]={'default_value':default,'last_value':last,'effective_value':selected[0],'source':selected[1],
            'version':row.get('version',0),'invalid_last':bool(last and not valid(db,role,last)),
            'invalid_default':bool(default and not valid(db,role,default))}
    return {'roles':result,'profiles':profiles}

def resolve_profile(store,role,requested=None):
    if requested:
        profile=store.one('SELECT * FROM llm_configs WHERE id=?',(requested,))
        if not profile:raise HTTPException(404,'模型配置不存在')
        return profile
    with store.connect() as db:
        data=snapshot(db);value=data['roles'][role]['effective_value']
    if value:return store.one('SELECT * FROM llm_configs WHERE id=?',(value,))
    if data['profiles']:raise HTTPException(409,'此功能未设置有效模型，请在运行管理中选择')
    return None  # Legacy unconfigured LM Studio is local-only.

def register_model_preferences(app,store):
    @app.get('/api/model-preferences')
    def read():
        with store.connect() as db:return snapshot(db)
    @app.put('/api/model-preferences/{role}/{kind}')
    def update(role:str,kind:str,body:ChoiceInput):
        if role not in ROLES or kind not in ('default','last'):raise HTTPException(404,'不存在的模型配置项')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if body.value and not valid(db,role,body.value):raise HTTPException(409,'模型配置已失效，请重新选择')
            old=db.execute('SELECT version FROM model_preferences WHERE role=?',(role,)).fetchone()
            version=old['version'] if old else 0
            if body.version is not None and body.version!=version:raise HTTPException(409,'配置已被其他页面修改，请刷新后重试')
            field='default_value' if kind=='default' else 'last_value'
            db.execute('INSERT INTO model_preferences(role,'+field+') VALUES(?,?) ON CONFLICT(role) DO UPDATE SET '+field+'=excluded.'+field+',version=version+1,updated=CURRENT_TIMESTAMP',(role,body.value))
            return snapshot(db)
    @app.post('/api/model-preferences/import')
    def import_choices(body:ImportInput):
        imported=[]
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for role,value in body.choices.items():
                if role not in ROLES or len(value)>100 or not valid(db,role,value):continue
                old=db.execute('SELECT last_value FROM model_preferences WHERE role=?',(role,)).fetchone()
                if old:continue  # Existing backend intent, including an explicit clear, wins migration.
                db.execute("INSERT INTO model_preferences(role,last_value) VALUES(?,?) ON CONFLICT(role) DO UPDATE SET last_value=excluded.last_value,version=version+1,updated=CURRENT_TIMESTAMP",(role,value));imported.append(role)
            return snapshot(db)|{'imported':imported}
