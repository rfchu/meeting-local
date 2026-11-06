"""Passive runtime inventory. Opening management never starts inference or probes."""
import json,os,shutil,time
from fastapi import HTTPException,Query
from .providers import engine_catalog,LocalDiarization,LocalPunctuation

def register_runtime_management(app,store,native,resident,probes,runtime=None):
    @app.get('/api/runtime/recordings')
    def recording_states(limit:int=Query(default=20,ge=1,le=50)):
        if runtime is None:raise HTTPException(503,'录音状态暂不可用')
        try:return runtime.inventory(limit)
        except (OSError,ValueError,KeyError,TypeError):raise HTTPException(503,'录音状态暂不可用，已有内容保留')

    @app.get('/api/runtime/overview')
    def overview():
        issues=[]
        def checked(label,fn,fallback):
            try:return fn()
            except (OSError,ValueError,RuntimeError):
                issues.append(label+'暂不可用');return fallback
        engines=checked('语音模型文件检查',engine_catalog,[])
        capability=checked('采集能力检查',native.capability,{})
        usage=checked('磁盘状态',lambda:shutil.disk_usage(store.root),None)
        sessions=[]
        for row in store.all("SELECT recording_id,source,config,status,cursor,updated FROM live_sessions WHERE status IN ('running','paused','draining') ORDER BY updated DESC LIMIT 50"):
            try:engine=json.loads(row['config']).get('engine','qwen')
            except ValueError:engine='unknown'
            sessions.append({k:row[k] for k in ('recording_id','source','status','updated')}|{'engine':engine,'confirmed_until':round(row['cursor']/16000,1)})
        jobs=store.all("SELECT id,meeting_id,kind,state,stage,progress,created FROM jobs WHERE state IN ('queued','running') ORDER BY created LIMIT 50")
        counts={r['state']:r['n'] for r in store.all('SELECT state,count(*) AS n FROM jobs GROUP BY state')}
        profiles=store.all('SELECT id,name,provider_type,model,is_default FROM llm_configs ORDER BY is_default DESC,created')
        return {'checked_at':time.time(),'passive':True,'issues':issues,'engines':engines,
            'native':capability,'capture_test_active':probes.active(),'qwen':resident.status(),
            'dependencies':{'ffmpeg':bool(shutil.which('ffmpeg')),
                'diarization':checked('发言人分析',lambda:LocalDiarization().ready(),False),
                'punctuation':checked('标点模型',lambda:LocalPunctuation().ready(),False)},
            'storage':{'data_dir':str(store.root),'writable':os.access(store.root,os.W_OK),
                'free_bytes':usage.free if usage else None,'total_bytes':usage.total if usage else None},
            'llm':{'profiles':profiles,'connection_checked':False},'sessions':sessions,'jobs':jobs,'job_counts':counts}
