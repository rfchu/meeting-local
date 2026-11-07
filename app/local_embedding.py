"""Pinned, offline ONNX embedding runtime and resumable model installation."""
import fcntl,hashlib,importlib.util,json,shutil,threading,time,urllib.request,zipfile
from functools import lru_cache
from pathlib import Path

MODEL_ID='bge-small-zh-v1.5-int8'
REVISION='75c43b069aac4d136ba6bc1122f995fedcfd2781'
BASE_URL=f'https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/{REVISION}/'
FILES={
 'onnx/model_int8.onnx':{'size':23903394,'sha256':'b9837c19ce154ff0726d398ee77abbc03a7faf0476c6f93016c84e531be7ebb5'},
 'tokenizer.json':{'size':439125,'sha256':'48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26'},
}
MANIFEST={'model':MODEL_ID,'revision':REVISION,'files':FILES,'dimension':512,'max_tokens':512,
          'pooling':'cls','normalization':'l2','query_prefix':'为这个句子生成表示以用于检索相关文章：','document_prefix':'',
          'windowing':'token-window-v1','window_tokens':480,'aggregation':'mean-normalized-cls','special_token_ids':[101,102],'runtime':'onnx-cpu'}
from .paths import model_root

MODEL_ROOT=model_root()/'embedding'

class InstallCancelled(Exception):pass

def checksum(path):
    result=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):result.update(block)
    return result.hexdigest()

def verify(folder):
    for name,info in FILES.items():
        path=folder/name
        if not path.is_file() or path.is_symlink() or path.stat().st_size!=info['size'] or checksum(path)!=info['sha256']:
            raise ValueError('模型文件校验失败，请重试安装或导入正确的固定版本模型包')

class LocalEmbedding:
    def __init__(self,root=MODEL_ROOT):
        self.root=Path(root);self.folder=self.root/MODEL_ID;self.staging=self.root/(MODEL_ID+'.partial')
        self.install_lock=threading.Lock();self.inference_lock=threading.RLock();self.cancel=threading.Event()
        self.session=None;self.tokenizer=None;self.timer=None;self.last_used=0;self.install_file=None
        self.state={'state':'idle','downloaded':0,'total':sum(x['size'] for x in FILES.values()),'error':''}
    def installed(self):
        try:return json.loads((self.folder/'manifest.json').read_text())==MANIFEST and all((self.folder/n).stat().st_size==v['size'] for n,v in FILES.items())
        except (OSError,ValueError):return False
    def status(self):
        return {**self.state,'installed':self.installed(),'loaded':self.session is not None,'model':MODEL_ID,
                'revision':REVISION,'dimension':512,'runtime_ready':all(importlib.util.find_spec(x) is not None for x in ('onnxruntime','tokenizers'))}
    def _publish(self,folder):
        verify(folder)
        (folder/'manifest.json').write_text(json.dumps(MANIFEST,ensure_ascii=False))
        with self.inference_lock:
            self.release()
            if self.folder.exists():shutil.rmtree(self.folder)
            folder.rename(self.folder)
    def _begin_install(self):
        if not self.install_lock.acquire(blocking=False):raise ValueError('模型安装正在进行')
        try:
            self.root.mkdir(parents=True,exist_ok=True)
            self.install_file=(self.root/'.install.lock').open('a')
            fcntl.flock(self.install_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,BlockingIOError):
            if self.install_file:self.install_file.close();self.install_file=None
            self.install_lock.release();raise ValueError('另一个进程正在安装模型，请稍后重试')
    def _end_install(self):
        if self.install_file:self.install_file.close();self.install_file=None
        self.install_lock.release()
    def start_install(self):
        self._begin_install()
        self.cancel.clear();self.state.update(state='downloading',error='',downloaded=0)
        threading.Thread(target=self._download,daemon=True).start()
        return self.status()
    def _download(self):
        try:
            self.root.mkdir(parents=True,exist_ok=True);self.staging.mkdir(exist_ok=True)
            completed=0
            for name,info in FILES.items():
                if self.cancel.is_set():raise InstallCancelled()
                path=self.staging/name;path.parent.mkdir(parents=True,exist_ok=True)
                offset=path.stat().st_size if path.exists() else 0
                if offset>info['size']:path.unlink();offset=0
                if offset==info['size'] and checksum(path)==info['sha256']:
                    completed+=offset;self.state['downloaded']=completed;continue
                if offset==info['size']:path.unlink();offset=0
                headers={'Range':f'bytes={offset}-'} if offset else {}
                # No text or user data in these fixed public download URLs.
                with urllib.request.urlopen(urllib.request.Request(BASE_URL+name,headers=headers),timeout=30) as response:
                    if response.status!=206:offset=0
                    elif not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                        raise ValueError('下载续传范围不匹配，请重试')
                    with path.open('ab' if offset else 'wb') as output:
                        while True:
                            if self.cancel.is_set():raise InstallCancelled()
                            block=response.read(256*1024)
                            if not block:break
                            if offset+len(block)>info['size']:raise ValueError('下载文件大小超过固定模型包上限')
                            output.write(block);offset+=len(block);self.state['downloaded']=completed+offset
                if offset!=info['size'] or checksum(path)!=info['sha256']:
                    path.unlink(missing_ok=True);raise ValueError('下载文件校验失败，请重试')
                completed+=offset
            if self.cancel.is_set():raise InstallCancelled()
            self._publish(self.staging);self.state.update(state='ready',error='')
        except InstallCancelled:self.state.update(state='cancelled',error='安装已取消，可继续下载')
        except Exception:self.state.update(state='failed',error='模型下载失败，可重试续传或导入离线包；不会影响已有会议')
        finally:self._end_install()
    def import_zip(self,path):
        self._begin_install()
        target=self.root/(MODEL_ID+'.import')
        try:
            self.state.update(state='importing',error='');self.root.mkdir(parents=True,exist_ok=True)
            shutil.rmtree(target,ignore_errors=True);target.mkdir()
            with zipfile.ZipFile(path) as archive:
                entries=archive.infolist();names=[x.filename for x in entries]
                allowed=set(FILES)|{'manifest.json'}
                if len(names)!=len(set(names)) or set(names)-allowed or not set(FILES)<=set(names):raise ValueError('离线包文件清单不匹配')
                for entry in entries:
                    if entry.filename=='manifest.json':
                        if entry.file_size>4096 or json.loads(archive.read(entry))!=MANIFEST:raise ValueError('离线包版本不匹配')
                        continue
                    if entry.file_size!=FILES[entry.filename]['size']:raise ValueError('离线包大小不匹配')
                    dest=target/entry.filename;dest.parent.mkdir(parents=True,exist_ok=True)
                    with archive.open(entry) as src,dest.open('wb') as out:shutil.copyfileobj(src,out,1024*1024)
            self._publish(target);self.state.update(state='ready',error='',downloaded=self.state['total'])
        except Exception:
            self.state.update(state='failed',error='离线包导入失败，需提供指定版本且校验一致的模型包');raise ValueError(self.state['error'])
        finally:shutil.rmtree(target,ignore_errors=True);self._end_install()
        return self.status()
    def _load(self):
        if self.session is not None:return
        if not self.installed():raise ValueError('请先安装内置中文检索模型')
        verify(self.folder)
        import onnxruntime as ort
        ort.disable_telemetry_events()
        from tokenizers import Tokenizer
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        try:
            session=ort.InferenceSession(str(self.folder/'onnx/model_int8.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
            tokenizer=Tokenizer.from_file(str(self.folder/'tokenizer.json'))
            tokenizer.no_truncation();tokenizer.no_padding()
        except Exception as error:raise ValueError('内置模型无法加载，请检查运行依赖并重新安装模型') from error
        self.session=session;self.tokenizer=tokenizer
    def release(self):
        with self.inference_lock:
            if self.timer:self.timer.cancel();self.timer=None
            self.session=None;self.tokenizer=None
    def _idle(self):
        with self.inference_lock:
            if time.monotonic()-self.last_used>=175:self.release()
    def encode(self,texts,query=False):
        import numpy as np
        with self.inference_lock:
            self._load();result=[]
            prefix=MANIFEST['query_prefix'] if query else ''
            for text in texts:
                # Split by tokenizer offsets; all content contributes, never silently truncate to 512.
                encoded=self.tokenizer.encode(prefix+text,add_special_tokens=False)
                windows=[]
                for begin in range(0,max(1,len(encoded.ids)),MANIFEST['window_tokens']):
                    ids=encoded.ids[begin:begin+MANIFEST['window_tokens']]
                    windows.append([101]+ids+[102])
                vectors=[]
                for begin in range(0,len(windows),8):
                    batch=windows[begin:begin+8];length=max(map(len,batch))
                    ids=np.array([w+[0]*(length-len(w)) for w in batch],dtype=np.int64)
                    masks=np.array([[1]*len(w)+[0]*(length-len(w)) for w in batch],dtype=np.int64)
                    inputs={'input_ids':ids,'attention_mask':masks,'token_type_ids':np.zeros_like(ids)}
                    try:output=self.session.run(None,{i.name:inputs[i.name] for i in self.session.get_inputs()})[0]
                    except Exception as error:raise ValueError('内置向量推理失败，请稍后重试') from error
                    vectors.extend(output[:,0,:] if output.ndim==3 else output)
                # Normalized mean of window CLS embeddings; fingerprint includes this policy.
                values=np.array(vectors,dtype=np.float32);values/=np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-12)
                vector=values.mean(axis=0);norm=np.linalg.norm(vector)
                if vector.shape!=(512,) or not np.isfinite(vector).all() or norm<=0:raise ValueError('内置模型输出无效')
                result.append((vector/norm).tolist())
            self.last_used=time.monotonic()
            if self.timer:self.timer.cancel()
            self.timer=threading.Timer(180,self._idle);self.timer.daemon=True;self.timer.start()
            return result

@lru_cache(maxsize=1)
def local_embedding():return LocalEmbedding()
