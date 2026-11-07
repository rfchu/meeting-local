"""Passive runtime inventory. Opening management never starts inference or probes."""
import json,os,shutil,time
from fastapi import HTTPException,Query
from fastapi.responses import JSONResponse
from .providers import engine_catalog,LocalDiarization,LocalPunctuation

def register_runtime_management(app,store,native,resident,probes,runtime=None,resources=None,live_sessions=None,acceptance=None):
    @app.get('/api/runtime/quality-baseline')
    def quality_baseline():
        from .quality_evaluation import baseline_inventory
        try:return JSONResponse(baseline_inventory(),headers={'Cache-Control':'no-store'})
        except (OSError,ValueError,KeyError,TypeError):raise HTTPException(503,'质量基线暂不可用，未运行模型或修改会议')

    @app.get('/api/runtime/release-readiness')
    def release_readiness():
        from .release_readiness import inspect_release
        from .paths import ROOT
        return JSONResponse(inspect_release(ROOT,acceptance),headers={'Cache-Control':'no-store'})

    @app.get('/api/runtime/capture-control')
    def capture_control():
        sessions=[]
        if live_sessions:
            for row in store.all('SELECT recording_id FROM live_sessions ORDER BY updated DESC LIMIT 50'):
                try:
                    value=live_sessions.recovery_status(row['recording_id'])
                    if value['task_active'] or value['recoverable']:sessions.append(value)
                except (OSError,ValueError,KeyError):pass
        control=None
        try:
            from .capture_broker import Client,endpoint,read_private
            folder,_,_=endpoint(native.recordings.root);registry=read_private(folder/'broker.json')
            observer=Client(native.recordings.root,registry['recording_id'],registry['incarnation'])
            observed=observer.status()
            control={key:observed.get(key) for key in ('recording_id','incarnation','broker_build','source_digest','started_at','alive','state','generation','can_claim','exit_code','exit_reason','pending_commands')}
        except (OSError,ValueError,KeyError):pass
        return JSONResponse({'passive':True,'sessions':sessions,'control':control,
            'upgrade':{'requires_idle':True,'policy':'结束录音和试录后升级；退出工作区不会结束独立采集，更新不会接管或停止录音。'}},headers={'Cache-Control':'no-store'})

    @app.get('/api/runtime/resources')
    def resource_status():
        if resources is None:raise HTTPException(503,'资源状态暂不可用')
        return JSONResponse(resources.snapshot(),headers={'Cache-Control':'no-store'})

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
