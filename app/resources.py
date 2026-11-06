"""C2 cooperative priority and process-local resource admission. Capture/storage never acquire these leases."""
import threading,time,uuid,math
from types import MappingProxyType
from collections import deque
from contextlib import contextmanager

CAPACITIES={'legacy_inference':1,'asr':1,'local_llm':1,'cloud_llm':1,'embedding':1}
LABELS={'legacy_inference':'共享推理通道','asr':'语音识别','local_llm':'本地 LLM','cloud_llm':'云端 LLM 请求','embedding':'向量计算'}
WORKLOADS={'live_asr','transcription','analysis','minutes','note_expansion','terminology','index','qa','semantic_search','embedding_config','live_overview','live_correction','speaker_review','legacy'}
PRIORITIES={name:(0 if name=='live_asr' else 10 if name in ('live_overview','live_correction') else 20 if name in ('semantic_search','embedding_config','speaker_review') else 40 if name=='index' else 30) for name in WORKLOADS}
REASONS={key+'_busy':'等待'+label+'资源' for key,label in LABELS.items()}|{'realtime_index_yield':'实时识别期间暂缓索引，录音结束后继续','closed':'资源协调器已关闭','realtime_priority':'会中识别优先，等待实时识别暂停或结束','priority_queue':'等待更高优先级或较早的资源请求'}

class ResourceBusy(ValueError):pass
class ResourceYielded(Exception):pass

class ResourceScheduler:
    def __init__(self,capacities=None,max_requests=128,history_limit=50):
        self.capacities=MappingProxyType(dict(CAPACITIES if capacities is None else capacities))
        if not self.capacities or any(k not in CAPACITIES or type(v) is not int or v<1 for k,v in self.capacities.items()):raise ValueError('资源额度无效')
        if not 1<=max_requests<=1024 or not 0<=history_limit<=200:raise ValueError('资源记录上限无效')
        self.condition=threading.Condition();self.used={k:0 for k in self.capacities}
        self.requests={};self.deferred={};self.deferred_total=0;self.observed={};self.history=deque(maxlen=history_limit)
        self.max_requests=max_requests;self.live_owners=set();self.sequence=0;self.closed=False;self.finished=0;self.rejected=0

    def request(self,workload,requirements,owner=None):
        if workload not in WORKLOADS:raise ValueError('未知资源请求类型')
        requirements=dict(requirements)
        if not requirements or any(k not in self.capacities or type(v) is not int or not 1<=v<=self.capacities[k] for k,v in requirements.items()):raise ValueError('资源请求超出额度或类型无效')
        return ResourceClaim(self,workload,requirements,owner)

    def set_realtime(self,owners):
        with self.condition:
            if self.closed:return
            values=set(owners)
            if values!=self.live_owners:self.live_owners=values;self.condition.notify_all()

    def should_yield(self,workload,requirements):
        with self.condition:
            return bool(self.live_owners and workload not in ('live_asr','live_overview','live_correction') and
                        ('legacy_inference' in requirements or 'local_llm' in requirements))

    def _reason(self,claim):
        if self.closed:return 'closed'
        if self.should_yield(claim.workload,claim.requirements):return 'realtime_priority'
        busy=next((key+'_busy' for key,units in claim.requirements.items() if self.used[key]+units>self.capacities[key]),'')
        if busy:return busy
        for other in self.requests.values():
            if other is claim or other.state!='waiting' or self.should_yield(other.workload,other.requirements):continue
            if (other.priority,other.order)<(claim.priority,claim.order) and set(other.requirements)&set(claim.requirements):return 'priority_queue'
        return ''

    def cancel_waiting(self,owner,workloads=None):
        with self.condition:
            claims=[claim for claim in self.requests.values() if claim.owner==owner and claim.state=='waiting' and (workloads is None or claim.workload in workloads)]
            for claim in claims:self._close(claim,'cancelled')
            observed=self.observed.get(owner)
            if observed and (workloads is None or observed['claim'].workload in workloads):self.observed.pop(owner,None)
            if owner in self.deferred:
                self.deferred.pop(owner);self.deferred_total=max(0,self.deferred_total-1)
            self.condition.notify_all()
            return len(claims)

    def admission_reason(self,workload,requirements):
        claim=self.request(workload,requirements)
        with self.condition:return self._reason(claim)

    def _acquire(self,claim,blocking,timeout):
        if not isinstance(timeout,(int,float)) or not math.isfinite(timeout):raise ValueError('资源等待超时无效')
        if not blocking and timeout!=-1:raise ValueError('非阻塞请求不能指定超时')
        if timeout<0 and timeout!=-1:raise ValueError('资源等待超时无效')
        deadline=None if timeout==-1 else time.monotonic()+timeout
        with self.condition:
            if claim.state in ('active','closed'):raise RuntimeError('资源请求不能重复获取或重新打开')
            if self.closed:raise ResourceBusy(REASONS['closed'])
            if claim.id not in self.requests:
                if len(self.requests)>=self.max_requests:
                    self.rejected+=1;raise ResourceBusy('资源请求数量达到上限，请稍后重试')
                self.sequence+=1;claim.order=self.sequence
                self.requests[claim.id]=claim;claim.state='waiting';claim.created=time.monotonic()
            while True:
                if claim.state=='closed':return False
                reason=self._reason(claim);claim.reason=reason
                if reason=='closed':raise ResourceBusy(REASONS[reason])
                if not reason:
                    for key,units in claim.requirements.items():self.used[key]+=units
                    claim.state='active';claim.started=time.monotonic();self.condition.notify_all();return True
                if not blocking:return False
                remaining=None if deadline is None else deadline-time.monotonic()
                if remaining is not None and remaining<=0:return False
                self.condition.wait(remaining)

    def _close(self,claim,outcome):
        if outcome not in ('released','withdrawn','busy','failed','cancelled'):outcome='withdrawn'
        with self.condition:
            if claim.state=='closed':return
            if claim.state=='active':
                for key,units in claim.requirements.items():self.used[key]-=units
            if self.requests.pop(claim.id,None) is not None:
                self.finished+=1
                self.history.append({'workload':claim.workload,'outcome':outcome,'wait_seconds':round((claim.started or time.monotonic())-claim.created,3)})
            claim.state='closed';self.condition.notify_all()

    def set_index_deferred(self,owners,total=None):
        owners=list(dict.fromkeys(owners))
        total=len(owners) if total is None else max(len(owners),int(total))
        owners=owners[:self.max_requests]
        with self.condition:
            self.deferred_total=total
            self.deferred={owner:self.deferred.get(owner) or {'id':uuid.uuid4().hex,'since':time.monotonic()} for owner in owners}

    def observe_wait(self,owner,workload,requirements,ttl=2):
        claim=self.request(workload,requirements,owner)
        with self.condition:
            if self.closed:return
            now=time.monotonic()
            self.observed={k:v for k,v in self.observed.items() if v['expires']>now}
            if owner not in self.observed and len(self.observed)>=self.max_requests:
                self.observed.pop(next(iter(self.observed)))
            previous=self.observed.get(owner)
            if previous:claim.id=previous['claim'].id
            self.observed[owner]={'claim':claim,'since':previous['since'] if previous else now,'expires':now+ttl}

    def clear_observed(self,owner):
        with self.condition:self.observed.pop(owner,None)

    def waiting_for(self,owner):
        with self.condition:
            if owner in self.deferred:return {'code':'realtime_index_yield','message':REASONS['realtime_index_yield']}
            claim=next((c for c in self.requests.values() if c.owner==owner and c.state=='waiting'),None)
            if not claim:
                observed=self.observed.get(owner)
                if not observed or observed['expires']<=time.monotonic():return None
                claim=observed['claim']
            reason=self._reason(claim)
            return {'code':reason or 'awaiting_dispatch','message':REASONS.get(reason,'等待资源调度')}

    def snapshot(self):
        with self.condition:
            now=time.monotonic();values=[]
            for claim in self.requests.values():
                reason=self._reason(claim) if claim.state=='waiting' else ''
                values.append({'id':claim.id,'workload':claim.workload,'state':claim.state,'requirements':dict(claim.requirements),'priority':claim.priority,
                    'wait_seconds':round((claim.started or now)-claim.created,3),'reason_code':reason,'reason':REASONS.get(reason,'')})
            for value in self.deferred.values():
                values.append({'id':value['id'],'workload':'index','state':'deferred','requirements':{'legacy_inference':1,'embedding':1},
                    'wait_seconds':round(now-value['since'],3),'reason_code':'realtime_index_yield','reason':REASONS['realtime_index_yield']})
            for value in self.observed.values():
                if value['expires']<=now:continue
                claim=value['claim'];reason=self._reason(claim)
                if not reason:continue
                values.append({'id':claim.id,'workload':claim.workload,'state':'deferred','requirements':dict(claim.requirements),'priority':claim.priority,
                    'wait_seconds':round(now-value['since'],3),'reason_code':reason,'reason':REASONS[reason]})
            return {'version':2,'checked_at':time.time(),'process_local':True,'closed':self.closed,'policy':'live_asr_priority_fifo_cooperative','live_session_count':len(self.live_owners),
                'resources':[{'id':key,'label':LABELS[key],'capacity':capacity,'used':self.used[key]} for key,capacity in self.capacities.items()],
                'requests':values,'history':list(self.history),'finished_count':self.finished,'rejected_count':self.rejected,
                'request_limit':self.max_requests,'deferred_limit':self.max_requests,'observed_limit':self.max_requests,'deferred_total':self.deferred_total,'deferred_truncated':self.deferred_total>len(self.deferred),
                'capture_independent':True,'memory_bytes_estimated':False,
                'coverage':['worker_jobs','editorial_jobs','live_asr','live_overview','live_correction','retrieval_api','inline_minutes','inline_speaker_review'],
                'excluded':['model_connection_tests','model_residency','external_processes']}

    def close(self):
        with self.condition:
            self.closed=True;self.live_owners.clear();self.deferred.clear();self.deferred_total=0;self.observed.clear();self.condition.notify_all()


class ResourceClaim:
    def __init__(self,scheduler,workload,requirements,owner):
        self.scheduler=scheduler;self.workload=workload;self.requirements=MappingProxyType(dict(requirements));self.owner=owner
        self.id=uuid.uuid4().hex;self.priority=PRIORITIES[workload];self.order=float('inf');self.state='new';self.created=None;self.started=None;self.reason=''
    def acquire(self,blocking=True,timeout=-1):return self.scheduler._acquire(self,blocking,timeout)
    def close(self,outcome=None):self.scheduler._close(self,outcome or ('released' if self.state=='active' else 'withdrawn'))
    def __enter__(self):
        try:
            if not self.acquire():raise ResourceBusy('资源请求已取消，未开始处理')
            return self
        except BaseException:self.close();raise
    def __exit__(self,kind,*_):self.close('failed' if kind else 'released')


class ResourceGate:
    """Lock-compatible legacy exclusion; resource bundles are acquired atomically."""
    def __init__(self,scheduler):
        if scheduler.capacities.get('legacy_inference')!=1:raise ValueError('共享推理通道必须保持互斥')
        self.scheduler=scheduler;self.claim=None;self.guard=threading.Lock()
    def request(self,workload='live_asr',requirements=None,owner=None):
        return self.scheduler.request(workload,{'legacy_inference':1,**(requirements or {'asr':1})},owner)
    def observe_wait(self,owner):self.scheduler.observe_wait(owner,'live_asr',{'legacy_inference':1,'asr':1})
    def clear_wait(self,owner):self.scheduler.clear_observed(owner)
    def acquire(self,blocking=True,timeout=-1):
        claim=self.request()
        try:
            if not claim.acquire(blocking,timeout):claim.close('busy');return False
            with self.guard:self.claim=claim
            return True
        except BaseException:claim.close();raise
    def release(self):
        with self.guard:
            claim=self.claim
            if claim is None:raise RuntimeError('不能释放尚未获取的资源锁')
            self.claim=None
        claim.close()
    def locked(self):
        with self.scheduler.condition:return bool(self.scheduler.used['legacy_inference'])
    def __enter__(self):self.acquire();return self
    def __exit__(self,*_):self.release()


class LegacyClaim:
    def __init__(self,lock):self.lock=lock;self.acquired=False
    def acquire(self,blocking=True,timeout=-1):
        self.acquired=self.lock.acquire(blocking,timeout);return self.acquired
    def close(self,*_):
        if self.acquired:self.lock.release();self.acquired=False


def request_for(lock,workload,requirements,owner=None):
    return lock.request(workload,requirements,owner) if isinstance(lock,ResourceGate) else LegacyClaim(lock)


@contextmanager
def optional_slot(scheduler,workload,resource,owner=None,legacy=False):
    if scheduler is None:yield;return
    claim=scheduler.request(workload,{**({'legacy_inference':1} if legacy else {}),resource:1},owner)
    try:
        if not claim.acquire(blocking=False):raise ResourceBusy(REASONS.get(claim.reason,'等待模型资源'))
        yield
    except BaseException:claim.close('failed' if claim.state=='active' else 'busy');raise
    else:claim.close()
