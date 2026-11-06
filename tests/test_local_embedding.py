import hashlib,io,json,zipfile
import numpy as np
import pytest
import app.local_embedding as module
from app.local_embedding import LocalEmbedding,MODEL_ID

@pytest.fixture
def tiny(tmp_path,monkeypatch):
    files={'onnx/model_int8.onnx':b'fixed model','tokenizer.json':b'fixed tokenizer'}
    specs={name:{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()}
    monkeypatch.setattr(module,'FILES',specs);monkeypatch.setattr(module,'MANIFEST',{**module.MANIFEST,'files':specs})
    return LocalEmbedding(tmp_path),files

def archive(files,extra=None):
    body=io.BytesIO()
    with zipfile.ZipFile(body,'w') as out:
        for name,data in files.items():out.writestr(name,data)
        for name,data in (extra or {}).items():out.writestr(name,data)
    body.seek(0);return body

def test_offline_import_exact_files_and_hash_tampering_keeps_old(tiny,tmp_path):
    m,files=tiny;m.import_zip(archive(files));assert m.installed()
    with pytest.raises(ValueError):m.import_zip(archive(files,{'../outside':b'no'}))
    assert m.installed() and not (tmp_path/'outside').exists()
    bad={**files,'tokenizer.json':b'bad tokenizer!!'}
    with pytest.raises(ValueError):m.import_zip(archive(bad))
    assert m.installed();m._load=lambda:None
    (m.folder/'onnx/model_int8.onnx').write_bytes(b'wrong model')
    with pytest.raises(ValueError):module.verify(m.folder)

def test_duplicate_entries_and_wrong_manifest_rejected(tiny):
    m,files=tiny
    with pytest.raises(ValueError):m.import_zip(archive(files,{'manifest.json':b'{"revision":"bad"}'}))
    with pytest.raises(ValueError):m.import_zip(archive({'tokenizer.json':files['tokenizer.json']}))
    assert not m.installed()

def test_resume_and_range_ignored_restart(tiny,monkeypatch):
    m,files=tiny;m.staging.mkdir();p=m.staging/'onnx/model_int8.onnx';p.parent.mkdir();p.write_bytes(files['onnx/model_int8.onnx'][:3]);requests=[]
    class Response(io.BytesIO):
        status=200;headers={}
    def download(request,timeout):
        name=request.full_url.removeprefix(module.BASE_URL);requests.append(request)
        return Response(files[name])
    monkeypatch.setattr(module.urllib.request,'urlopen',download)
    m.install_lock.acquire();m._download()
    assert requests[0].get_header('Range')=='bytes=3-'
    assert m.state['state']=='ready' and m.installed()
    module.verify(m.folder)

def test_valid_partial_content_resume(tiny,monkeypatch):
    m,files=tiny;m.staging.mkdir();p=m.staging/'onnx/model_int8.onnx';p.parent.mkdir();p.write_bytes(files['onnx/model_int8.onnx'][:3])
    class Response(io.BytesIO):
        status=206;headers={'Content-Range':'bytes 3-10/11'}
    def download(request,timeout):
        name=request.full_url.removeprefix(module.BASE_URL)
        if name=='onnx/model_int8.onnx':return Response(files[name][3:])
        response=Response(files[name]);response.status=200;response.headers={};return response
    monkeypatch.setattr(module.urllib.request,'urlopen',download)
    m.install_lock.acquire();m._download();assert m.installed()

def test_failed_download_cancel_and_retry_lock(tiny,monkeypatch):
    m,files=tiny
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    m.install_lock.acquire();m._download();assert m.state['state']=='failed' and not m.install_lock.locked()
    m.cancel.set();m.install_lock.acquire();m._download();assert m.state['state']=='cancelled' and not m.installed()

def test_overlength_uses_every_token_without_truncation_and_normalizes(tiny):
    m,_=tiny;seen=[]
    class Tokenizer:
        def encode(self,text,add_special_tokens):return type('Encoding',(),{'ids':list(range(200,1400))})()
    class Session:
        def get_inputs(self):return [type('Input',(),{'name':x})() for x in ('input_ids','attention_mask','token_type_ids')]
        def run(self,output,inputs):
            seen.extend(inputs['input_ids'][i][inputs['attention_mask'][i].astype(bool)][1:-1].tolist() for i in range(len(inputs['input_ids'])))
            return [np.ones((len(inputs['input_ids']),1,512),dtype=np.float32)]
    m.session=Session();m.tokenizer=Tokenizer()
    vectors=m.encode(['超长测试']);assert [token for window in seen for token in window]==list(range(200,1400))
    assert max(map(len,seen))<=480 and np.linalg.norm(vectors[0])==pytest.approx(1)
    m.last_used=0;m._idle();assert m.session is None and m.tokenizer is None

def test_builtin_config_and_index_flow(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.retrieval import Retrieval
    import app.main as main
    import app.retrieval as retrieval
    class Runtime:
        def installed(self):return True
        def status(self):return {'runtime_ready':True}
        def encode(self,texts,query=False):return [[1.0]+[0.0]*511 for _ in texts]
    runtime=Runtime();monkeypatch.setattr(main,'local_embedding',lambda:runtime);monkeypatch.setattr(retrieval,'local_embedding',lambda:runtime)
    # No lifespan to avoid changing any real runtime or model state.
    app=create_app(tmp_path,start_worker=False);client=TestClient(app);store=app.state.store
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',('m','会议','test.wav'))
    store.save_revision('m',[{'id':'s','start':0,'end':10,'speaker':'甲','text':'周五交付。'}],'manual')
    cfg=client.put('/api/retrieval/config',json={'provider':'builtin'});assert cfg.status_code==200
    config=cfg.json();assert config['model']==MODEL_ID and config['dimension']==512
    r=Retrieval(store);assert r.profile(config)['provider_type']=='builtin'
    from app.db import encode,uid
    jid=uid()
    store.execute("INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)",(jid,'','running','test',encode(config),'retrieval_index'))
    class Worker:
        def check(self,jid):pass
        def update(self,*args):pass
    r.build(Worker(),store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert r.status()['indexed']==1
    assert r.search('交付','semantic')['results'][0]['meeting_id']=='m'
    changed={**config,'manifest':'wrong'}
    with pytest.raises(ValueError):r.profile(changed)
    assert client.put('/api/retrieval/config',json={'provider':'builtin'}).status_code==409

def test_install_lock_shared_by_separate_managers(tiny):
    first,_=tiny;second=LocalEmbedding(first.root)
    first._begin_install()
    try:
        with pytest.raises(ValueError):second._begin_install()
        assert not second.install_lock.locked()
    finally:first._end_install()
    second._begin_install();second._end_install()

def test_load_failure_does_not_publish_half_initialized_session(tiny,monkeypatch):
    m,files=tiny;m.import_zip(archive(files))
    import onnxruntime as ort
    monkeypatch.setattr(ort,'InferenceSession',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('bad runtime')))
    with pytest.raises(ValueError,match='无法加载'):m._load()
    assert m.session is None and m.tokenizer is None

def test_range_mismatch_and_oversized_download_rejected(tiny,monkeypatch):
    m,files=tiny
    class Response(io.BytesIO):
        status=206;headers={'Content-Range':'bytes 2-10/11'}
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *a,**k:Response(b'untrusted'))
    m.install_lock.acquire();m._download();assert m.state['state']=='failed' and not m.installed()
    class TooBig(io.BytesIO):status=200;headers={}
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *a,**k:TooBig(b'X'*100))
    m.install_lock.acquire();m._download();assert m.state['state']=='failed' and not m.installed()
