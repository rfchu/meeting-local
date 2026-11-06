"""Safe operational views: never expose model logs, prompts, credentials or audio."""
import json,re,time
from fastapi import HTTPException
from fastapi.responses import JSONResponse

KINDS={'transcription':'音频转写','analysis':'会议分析','minutes':'AI 会议纪要','retrieval_index':'语义索引','retrieval_answer':'引用问答','terminology':'术语提取','note_expansion':'简记整理'}

def classify(error):
    value=(error or '').lower()
    rules=[('auth',r'401|403|api.?key|认证|unauthorized','模型服务认证失败','检查 API Key 和服务权限','models'),
        ('timeout',r'timeout|timed out|超时|超过.*时限','模型处理超时','检查服务负载，或从会议中缩小输入后重新发起','models'),
        ('reference',r'引用|证据|citation','原文依据校验未通过','核对文字稿或重新生成，原有结果保留','meeting'),
        ('format',r'json|decode|结构|格式','模型返回格式不完整','检查模型是否适合结构化输出，或重试','models'),
        ('connection',r'connection|连接|urlerror|无法访问','模型服务连接失败','确认本机或云端服务已启动且地址正确','models'),
        ('changed',r'已变化|已删除|不存在|版本|指纹|不一致','任务绑定的模型或资料已变化','回到对应会议重新发起；不要重复提交旧配置','meeting'),
        ('model',r'未安装|未就绪|缺少|模型文件','本机能力尚未就绪','检查模型文件和处理依赖','environment'),
        ('resource',r'资源|正在使用|busy','处理资源暂时占用','等待当前处理完成后重试','tasks')]
    for code,pattern,summary,advice,target in rules:
        if re.search(pattern,value):return {'code':code,'summary':summary,'advice':advice,'target':target}
    return {'code':'unknown','summary':'任务未完成','advice':'可重试；若重复失败，回到会议检查输入并导出脱敏诊断','target':'meeting'}

def retry_blocker(store,job,worker):
    if job['state'] not in ('failed','cancelled'):return '只有失败或已取消的任务可重试'
    if job.get('kind') not in (None,*KINDS):return '当前版本不支持此任务类型，请从原功能重新发起'
    if worker.busy_jid==job['id']:return '原处理进程正在退出，请稍后重试'
    if job.get('meeting_id') and not store.one('SELECT 1 FROM meetings WHERE id=?',(job['meeting_id'],)):return '会议已删除，不能恢复此任务'
    try:config=json.loads(job['config'])
    except (ValueError,TypeError):return '任务配置不完整，请回到会议重新发起'
    if not isinstance(config,dict):return '任务配置不完整，请回到会议重新发起'
    if job.get('kind')=='retrieval_answer':
        qa=store.one('SELECT config,cloud_confirmed FROM qa_runs WHERE id=?',(config.get('qa_id'),))
        if not qa:return '本次问答依据已清理，请重新检索后提问'
        from .qa import provider_signature
        qconfig=json.loads(qa['config']);profile=store.one('SELECT * FROM llm_configs WHERE id=?',(qconfig.get('profile_id'),))
        if not profile or provider_signature(profile)!=qconfig.get('signature'):return '问答模型配置已变化，请重新检索并确认发送目标'
    if config.get('profile_id'):
        profile=store.one('SELECT * FROM llm_configs WHERE id=?',(config['profile_id'],))
        if not profile:return '原模型配置已删除，请回到会议选择模型后重新发起'
        bound=config.get('provider') or {}
        if not isinstance(bound,dict):return '原模型快照不完整，请回到会议重新发起'
        if any(k in bound and bound[k]!=profile[k] for k in ('provider_type','base_url')):return '原模型服务地址已变化，请回到会议重新核对后发起'
    if job.get('kind')=='retrieval_index':
        row=store.one('SELECT config FROM retrieval_settings WHERE id=1')
        if not row or json.loads(row['config'])!=config:return '索引模型已变化，请从数据与索引重新建立'
    if store.one("SELECT 1 FROM jobs WHERE meeting_id=? AND state IN ('queued','running')",(job['meeting_id'],)):return '同一会议已有任务正在处理'
    return ''

def register_task_management(app,store,worker,resources=None):
    def records(state,limit):
        if state not in ('active','failed','all'):raise HTTPException(400,'未知任务筛选')
        where="WHERE j.state IN ('queued','running')" if state=='active' else "WHERE j.state IN ('failed','cancelled')" if state=='failed' else ''
        rows=store.all("SELECT j.*,m.title AS meeting_title FROM jobs j LEFT JOIN meetings m ON m.id=j.meeting_id "+where+" ORDER BY CASE j.state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,CASE WHEN j.state IN ('queued','running') THEN j.created END,CASE WHEN j.state NOT IN ('queued','running') THEN j.created END DESC,j.rowid LIMIT ?",(limit,))
        live=bool(store.one("SELECT 1 FROM live_sessions WHERE status IN ('running','paused','draining') LIMIT 1"))
        queued=store.all("SELECT id,kind FROM jobs WHERE state='queued' ORDER BY created,rowid")
        processing=bool(worker.busy_jid or store.one("SELECT 1 FROM jobs WHERE state='running' LIMIT 1"))
        positions={r['id']:i+1 for i,r in enumerate(r for r in queued if not (live and r['kind']=='retrieval_index'))}
        values=[]
        for job in rows:
            try:config=json.loads(job['config'])
            except (ValueError,TypeError):config={}
            if not isinstance(config,dict):config={}
            blocked=retry_blocker(store,job,worker)
            wait='实时识别期间暂缓索引，录音结束后继续' if live and job['kind']=='retrieval_index' and job['state']=='queued' else ('等待处理资源' if job['state']=='running' and '等待' in (job['stage'] or '') else (('等待前序任务' if processing or positions.get(job['id'],1)>1 else '等待调度启动') if job['state']=='queued' else ''))
            resource_wait=resources.waiting_for(job['id']) if resources else None
            if resource_wait:wait=resource_wait['message']
            values.append({k:job[k] for k in ('id','meeting_id','meeting_title','kind','state','stage','progress','created')}|{
                'resource_wait':resource_wait,'kind_label':KINDS.get(job['kind'],'后台处理'),'queue_position':positions.get(job['id']), 'waiting_reason':wait,
                'model':config.get('model') or config.get('engine') or '', 'revision':config.get('revision',config.get('base_revision')),
                'failure':classify(job['error']) if job['state']=='failed' else None,
                'can_cancel':job['state'] in ('queued','running'),'can_retry':not blocked,'retry_blocked':blocked,
                'recorded_retries':store.one("SELECT count(*) AS n FROM job_runtime_events WHERE job_id=? AND action='retry'",(job['id'],))['n'],
                'exiting':worker.busy_jid==job['id'] and job['state']=='cancelled'})
        return values
    @app.get('/api/runtime/tasks')
    def tasks(state:str='active',limit:int=50):
        if not 1<=limit<=100:raise HTTPException(400,'任务数量范围为 1–100')
        values=records(state,limit)
        counts={r['state']:r['n'] for r in store.all('SELECT state,count(*) AS n FROM jobs GROUP BY state')}
        # Live overview failures are separate from durable meeting jobs.
        overview=[]
        for row in store.all("SELECT recording_id,state,enabled,failures,error FROM live_overview_runs WHERE enabled=1 OR failures>0 ORDER BY updated DESC LIMIT 20"):
            overview.append({k:row[k] for k in ('recording_id','state','enabled','failures')}|{'published':bool(store.one('SELECT 1 FROM meetings WHERE id=?',(row['recording_id'],))),'failure':classify(row['error']) if row['error'] and row['failures'] else None})
        return {'checked_at':time.time(),'items':values,'counts':counts,'live_overview':overview,'limit':limit}
    @app.get('/api/runtime/diagnostics')
    def diagnostics():
        data={'version':1,'created_at':time.time(),'counts':{r['state']:r['n'] for r in store.all('SELECT state,count(*) AS n FROM jobs GROUP BY state')},
            'tasks':[{k:job[k] for k in ('kind','state','progress','created')}|{'error_code':classify(job['error'])['code'] if job['state']=='failed' else None}
                for job in store.all('SELECT kind,state,progress,created,error FROM jobs ORDER BY created DESC LIMIT 100')]}
        if resources:data['resource_runtime']=resources.snapshot()
        return JSONResponse(data,headers={'Content-Disposition':'attachment; filename="tingji-runtime-diagnostics.json"','Cache-Control':'no-store'})
