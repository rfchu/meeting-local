"""Durable UI status and per-meeting exclusion for long synchronous minutes requests."""
import fcntl,json,time
from functools import wraps
from fastapi import HTTPException
from .db import encode,uid
from .resources import ResourceYielded
from .job_checkpoints import ResourceInterrupted

class MinutesGeneration:
    def __init__(self,store):self.store=store
    def _write(self,folder,value):
        path=folder/'minutes-generation.json';temp=folder/('minutes-generation-'+uid()+'.tmp')
        temp.write_text(encode(value));temp.replace(path)
    def status(self,mid):
        folder=self.store.directory(mid);path=folder/'minutes-generation.json'
        try:value=json.loads(path.read_text())
        except (OSError,ValueError):return None
        if not isinstance(value,dict) or not isinstance(value.get('started_at'),(int,float)):return None
        if value.get('state')=='waiting':
            pending=self.store.one('SELECT state FROM jobs WHERE id=?',(value.get('job_id'),))
            if not pending or pending['state'] not in ('queued','running'):
                with (folder/'minutes-generation.lock').open('a') as handle:
                    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:pass
                    else:
                        try:current=json.loads(path.read_text())
                        except (OSError,ValueError):current=value
                        if not isinstance(current,dict):current=value
                        if current.get('id')==value.get('id') and current.get('state')=='waiting':
                            job=self.store.one('SELECT state,error FROM jobs WHERE id=?',(value.get('job_id'),))
                            if not job or job['state'] in ('failed','cancelled','completed'):
                                artifact=self.store.one("SELECT id FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes' AND json_extract(provenance,'$.job')=? LIMIT 1",(mid,value.get('job_id'))) if job and job['state']=='completed' else None
                                state='completed' if artifact else 'cancelled' if job and job['state']=='cancelled' else 'failed'
                                current.update(state=state,error='' if artifact else ('纪要任务已取消' if state=='cancelled' else '等待中的纪要任务已结束，请重新核对'),finished_at=time.time())
                                if artifact:current['artifact_id']=artifact['id']
                                self._write(folder,current)
                        value=current
        if value.get('state')=='running':
            with (folder/'minutes-generation.lock').open('a') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:
                    # A process exit releases the lock; never leave a permanent spinner.
                    completed=self.store.one("SELECT a.id FROM artifacts a JOIN jobs j ON j.id=json_extract(a.provenance,'$.job') WHERE a.meeting_id=? AND a.kind='meeting_minutes' AND j.id=? AND j.state='completed' LIMIT 1",(mid,value.get('job_id'))) if value.get('job_id') else None
                    pending=self.store.one('SELECT state FROM jobs WHERE id=?',(value.get('job_id'),)) if value.get('job_id') else None
                    if pending and pending['state'] in ('queued','running'):
                        value.update(state='waiting',error='',message='任务等待恢复；继续时重新校验检查点');value.pop('finished_at',None)
                    elif pending and pending['state']=='cancelled':value.update(state='cancelled',error='纪要任务已取消',finished_at=time.time())
                    elif completed:value.update(state='completed',artifact_id=completed['id'],error='',finished_at=time.time())
                    else:value.update(state='failed',error='纪要生成进程已中断，请重新生成；已有纪要仍保留。',finished_at=time.time())
                    self._write(folder,value)
        return {**value,'elapsed_seconds':round(max(0,(value.get('finished_at') or time.time())-value['started_at']))}
    def track(self,operation):
        @wraps(operation)
        def wrapped(mid,body):
            row=self.store.one('SELECT revision FROM meetings WHERE id=?',(mid,))
            if not row:raise HTTPException(404,'会议不存在')
            folder=self.store.directory(mid);folder.mkdir(exist_ok=True)
            with (folder/'minutes-generation.lock').open('a') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise HTTPException(409,'本场会议已有纪要正在生成，请等待当前请求完成')
                try:previous=json.loads((folder/'minutes-generation.json').read_text())
                except (OSError,ValueError):previous={}
                if not isinstance(previous,dict):previous={}
                if previous.get('state') in ('running','waiting') and previous.get('job_id') and previous['job_id']!=getattr(body,'job_id',None):
                    pending=self.store.one('SELECT state FROM jobs WHERE id=?',(previous['job_id'],))
                    if pending and pending['state'] in ('queued','running'):raise HTTPException(409,'本场会议已有纪要任务等待或执行，请先完成或取消该任务')
                status={'id':uid(),'job_id':getattr(body,'job_id',None),'state':'running','started_at':time.time(),'revision':row['revision'],'error':''}
                self._write(folder,status)
                try:result=operation(mid,body)
                except (ResourceYielded,ResourceInterrupted) as interrupted:
                    status.update(state='waiting',error='',message='服务已停止，等待恢复' if isinstance(interrupted,ResourceInterrupted) else '会中识别优先，等待继续')
                    self._write(folder,status);raise
                except BaseException as error:
                    message=getattr(error,'detail',str(error)) or '纪要生成中断，请重试'
                    cancelled=self.store.one("SELECT state FROM jobs WHERE id=?",(status.get('job_id'),)) if status.get('job_id') else None
                    status.update(state='cancelled' if cancelled and cancelled['state']=='cancelled' else 'failed',error=str(message)[:1500],finished_at=time.time());self._write(folder,status)
                    raise
                status.update(state='completed',artifact_id=result['id'],finished_at=time.time());self._write(folder,status)
                return result
        return wrapped
