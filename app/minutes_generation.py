"""Durable UI status and per-meeting exclusion for long synchronous minutes requests."""
import fcntl,json,time
from functools import wraps
from fastapi import HTTPException
from .db import encode,uid

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
        if value.get('state')=='running':
            with (folder/'minutes-generation.lock').open('a') as handle:
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:pass
                else:
                    # A process exit releases the lock; never leave a permanent spinner.
                    value.update(state='failed',error='纪要生成进程已中断，请重新生成；已有纪要仍保留。',finished_at=time.time())
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
                status={'id':uid(),'state':'running','started_at':time.time(),'revision':row['revision'],'error':''}
                self._write(folder,status)
                try:result=operation(mid,body)
                except BaseException as error:
                    message=getattr(error,'detail',str(error)) or '纪要生成中断，请重试'
                    status.update(state='failed',error=str(message)[:1500],finished_at=time.time());self._write(folder,status)
                    raise
                status.update(state='completed',artifact_id=result['id'],finished_at=time.time());self._write(folder,status)
                return result
        return wrapped
