"""Versioned, content-bound checkpoints. SQLite remains the publication authority."""
import hashlib,json,os,re,time
from pathlib import Path
from .db import uid

PROTOCOL=1
PIPELINE='c3-validated-phases-v1'

class ResourceInterrupted(Exception):pass


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def atomic_json(path,value):
    path=Path(path);temporary=path.with_name('.'+path.name+'.'+uid()+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as handle:
            os.chmod(temporary,0o600)
            json.dump(value,handle,ensure_ascii=False,allow_nan=False)
            handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,path)
        descriptor=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


def job_binding(job):
    return digest([PIPELINE,job['id'],job.get('meeting_id'),job.get('kind') or 'transcription',json.loads(job['config'])])


def job_folder(store,job):
    if not re.fullmatch('[0-9a-f]{32}',job['id']):raise ValueError('任务标识无效')
    return (store.directory(job['meeting_id']) if job.get('meeting_id') else store.root/'job-work')/job['id']


def valid_meta(value,job):
    try:
        return (isinstance(value,dict) and value.get('protocol')==PROTOCOL and value.get('binding')==job_binding(job)
            and isinstance(value.get('committed'),dict) and all(type(value.get(k)) is int and value[k]>=0 for k in ('reused','rejected')))
    except (ValueError,KeyError,TypeError):return False


class JobCheckpoints:
    def __init__(self,store,job):
        self.store=store;self.job=job
        self.folder=job_folder(store,job)
        self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.folder/'recovery.json'
        self.binding=job_binding(job)
        self.existing=self.path.exists()
        if self.existing:
            try:self.meta=json.loads(self.path.read_text())
            except (ValueError,OSError):raise ValueError('检查点恢复记录损坏，请从原功能新建任务') from None
            if not isinstance(self.meta,dict) or self.meta.get('protocol')!=PROTOCOL or self.meta.get('binding')!=self.binding:raise ValueError('检查点版本或任务配置已变化，请从原功能新建任务')
            if not isinstance(self.meta.get('committed'),dict) or any(type(self.meta.get(k)) is not int or self.meta[k]<0 for k in ('reused','rejected')):raise ValueError('检查点恢复记录结构无效，请新建任务')
        else:
            self.meta={'protocol':PROTOCOL,'binding':self.binding,'committed':{},'reused':0,'rejected':0,'last_phase':None}
            self.flush()
    def flush(self):
        self.meta['updated_at']=time.time();atomic_json(self.path,self.meta)
    def _path(self,name):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,119}',name):raise ValueError('检查点阶段名称无效')
        return self.folder/(name+'.c3.json')
    def load(self,name,inputs,validate=lambda value:value):
        path=self._path(name)
        try:value=json.loads(path.read_text())
        except FileNotFoundError:
            if name in self.meta['committed']:self.reject(name)
            return None
        except ValueError:self.reject(name);return None
        try:
            if not isinstance(value,dict) or value.get('protocol')!=PROTOCOL or value.get('binding')!=self.binding or value.get('input')!=digest(inputs) or value.get('checksum')!=digest(value['payload']):raise ValueError('checkpoint mismatch')
            result=validate(value['payload'])
        except (ValueError,KeyError,TypeError):self.reject(name);return None
        self.meta['committed'][name]=value['checksum'];self.meta['last_phase']=name;self.meta['reused']+=1;self.flush()
        return result
    def reject(self,name):
        self.meta['committed'].pop(name,None);self.meta['rejected']+=1;self.flush()
    def save(self,name,payload,inputs,validate=lambda value:value):
        result=validate(payload)
        value={'protocol':PROTOCOL,'binding':self.binding,'input':digest(inputs),'checksum':digest(payload),'payload':payload}
        atomic_json(self._path(name),value)
        self.meta['committed'][name]=value['checksum'];self.meta['last_phase']=name;self.flush()
        return result
    def file_valid(self,name,path,inputs):
        def validate(value):
            if not isinstance(value,dict) or not Path(path).is_file() or value.get('sha256')!=file_digest(path):raise ValueError('检查点文件已变化')
            return value
        return self.load(name,inputs,validate) is not None
    def save_file(self,name,path,inputs):
        with Path(path).open('rb') as handle:os.fsync(handle.fileno())
        self.save(name,{'sha256':file_digest(path)},inputs)


def recovery_view(store,job):
    """Bounded metadata only; never return paths, digests, prompts or contents."""
    try:
        folder=job_folder(store,job)
        value=json.loads((folder/'recovery.json').read_text())
    except (OSError,ValueError):return {'protocol':None,'checkpoints':0,'message':'尚无可信检查点；未完成阶段会重新计算'}
    if not valid_meta(value,job):return {'protocol':None,'checkpoints':0,'message':'恢复记录不可用，请从原功能新建任务'}
    count=len(value['committed'])
    return {'protocol':PROTOCOL,'checkpoints':count,'reused':value.get('reused',0),'rejected':value.get('rejected',0),
            'message':('已完成；产物以数据库提交为准' if job['state']=='completed' else '已取消；保留检查点，重试时重新校验' if job['state']=='cancelled' else '恢复时校验输入与结果，仅重算未提交或不匹配阶段')}


def validate_segments(value,duration):
    import math
    if not isinstance(value,list):raise ValueError('转写检查点格式无效')
    ids=set()
    for item in value:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in ids or not isinstance(item.get('text'),str) or not isinstance(item.get('speaker'),str):raise ValueError('转写检查点片段无效')
        ids.add(item['id'])
        start,end=item.get('start'),item.get('end')
        if any(type(n) not in (int,float) or not math.isfinite(n) for n in (start,end)) or not 0<=start<=end<=duration+1/16000:raise ValueError('转写检查点时间戳无效')
    return value


def recovery_blocker(store,job):
    try:
        path=job_folder(store,job)/'recovery.json'
        if not path.exists():return ''
        if valid_meta(json.loads(path.read_text()),job):return ''
    except (OSError,ValueError,KeyError,TypeError):
        # Invalid legacy IDs have no C3 checkpoints; preserve compatibility APIs.
        if not re.fullmatch('[0-9a-f]{32}',job['id']):return ''
    return '检查点版本或恢复记录不匹配，请从原功能新建任务'


def model_files(paths):
    values=[]
    for path in paths:
        path=Path(path)
        try:
            stat=path.stat();values.append([str(path),stat.st_size,stat.st_mtime_ns])
        except FileNotFoundError:values.append([str(path),None,None])
    return values
