import json,math
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.retrieval import Retrieval,embeddings
from app.db import uid,encode
from app.worker import Cancelled

@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as c:
        store=app.state.store;ids=[]
        for title,text in [('项目评审','周五完成接口联调，李工负责。'),('销售例会','本周客户回款已确认。')]:
            mid=uid();ids.append(mid);store.directory(mid).mkdir(exist_ok=True)
            store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,title,'test.wav',100))
            store.save_revision(mid,[{'id':'s1','start':12,'end':20,'text':text,'speaker':'李工'}],'manual',expected=0)
        pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,?,?,?)',(pid,'本机','local','http://127.0.0.1:1234/v1','chat'))
        def fake(profile,model,texts,dimension=None):
            if model=='embed2':return [[0,1,0] for _ in texts]
            return [[1,0] if '联调' in t or '接口' in t or '开发' in t else [0,1] for t in texts]
        monkeypatch.setattr('app.retrieval.embeddings',fake)
        yield c,store,Retrieval(store),ids,pid

def configure(setup,model='embed'):
    c,_,_,_,pid=setup;r=c.put('/api/retrieval/config',json={'profile_id':pid,'model':model});assert r.status_code==200;return r.json()

def build(setup,force=False):
    c,store,_,_,_=setup;r=c.post('/api/retrieval/build',json={'force':force});assert r.status_code==201
    jid=r.json()['id'];store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    c.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    store.execute("UPDATE jobs SET state='completed' WHERE id=?",(jid,))
    return jid

def test_keyword_search_without_model_and_stale_result(setup):
    c,store,r,ids,_=setup
    results=c.post('/api/retrieval/search',json={'query':'接口'}).json()['results'];assert len(results)==1
    hit=results[0];assert hit['meeting_id']==ids[0] and hit['start']==12 and hit['source_segment_ids']==['s1']
    assert c.post('/api/retrieval/resolve',json={'id':hit['id'],'fingerprint':hit['fingerprint']}).status_code==200
    store.save_revision(ids[0],[{'id':'s1','start':14,'end':22,'text':'联调已取消。','speaker':'李工'}],'manual',expected=1)
    assert c.post('/api/retrieval/resolve',json={'id':hit['id'],'fingerprint':hit['fingerprint']}).status_code==409
    assert c.post('/api/retrieval/search',json={'query':'接口'}).json()['results']==[]
    assert c.post('/api/retrieval/search',json={'query':'开发','mode':'semantic'}).status_code==409

def test_cosine_search_filters_and_incremental_model_change(setup):
    c,store,r,ids,_=setup;config=configure(setup);build(setup)
    assert r.status()['indexed']==2
    results=c.post('/api/retrieval/search',json={'query':'开发','mode':'semantic'}).json()['results'];assert results[0]['meeting_id']==ids[0]
    assert c.post('/api/retrieval/search',json={'query':'开发','mode':'semantic','meeting_id':ids[1]}).json()['results'][0]['meeting_id']==ids[1]
    unchanged=store.one('SELECT embedding FROM retrieval_units WHERE meeting_id=?',(ids[1],))['embedding']
    store.save_revision(ids[0],[{'id':'s2','start':21,'end':24,'text':'接口联调完成。','speaker':'李工'}],'manual',expected=1)
    assert r.status()['pending']==1 and r.status()['indexed']==1
    assert store.one('SELECT embedding FROM retrieval_units WHERE meeting_id=?',(ids[1],))['embedding']==unchanged
    build(setup);assert r.status()['pending']==0
    config2=configure(setup,'embed2');assert config2['identity']!=config['identity'] and config2['dimension']==3
    assert r.status()['indexed']==0
    build(setup);assert r.status()['indexed']==2

def test_notes_minutes_candidates_and_expansion_staleness(setup):
    c,store,r,ids,_=setup;mid=ids[0]
    nid=c.post(f'/api/meetings/{mid}/notes',json={'raw_text':'联调待确认','captured_at':15}).json()['id']
    store.execute("UPDATE meeting_notes SET accepted_text='联调由李工负责',status='accepted',expansion_revision=1,source_segment_ids=? WHERE id=?",(encode(['s1']),nid))
    for kind,provenance in [('note_expansion',{}),('meeting_minutes',{'edited':False})]:
        store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(uid(),mid,1,kind,encode({'decisions':[{'content':'联调已经完成','source_segment_ids':['s1']}]}),encode(provenance)))
    r.sync();assert {json.loads(x['metadata'])['kind'] for x in store.all('SELECT metadata FROM retrieval_units WHERE meeting_id=?',(mid,))}=={'transcript','note','expanded_note'}
    store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(uid(),mid,1,'meeting_minutes',encode({'decisions':[{'content':'联调继续','source_segment_ids':['s1']},{'content':'错误引用','source_segment_ids':['bad']}]}),encode({'edited':True})))
    assert len(c.post('/api/retrieval/search',json={'query':'联调','kind':'minutes'}).json()['results'])==1
    store.save_revision(mid,[{'id':'s1','start':12,'end':20,'text':'联调取消','speaker':'李工'}],'manual',expected=1)
    r.sync();assert {json.loads(x['metadata'])['kind'] for x in store.all('SELECT metadata FROM retrieval_units WHERE meeting_id=?',(mid,))}=={'transcript','note'}

def test_delete_cleans_index_and_no_model_cloud_request(setup):
    c,store,r,ids,pid=setup;r.sync()
    store.execute("UPDATE llm_configs SET provider_type='cloud',base_url='https://example.com/v1',api_key='test' WHERE id=?",(pid,))
    # Test the actual profile guard separately: fake embedding function does not guard profiles.
    with pytest.raises(ValueError):embeddings(store.one('SELECT * FROM llm_configs WHERE id=?',(pid,)),'embed',['text'])
    assert c.delete(f'/api/meetings/{ids[0]}').status_code==200
    assert not store.all('SELECT * FROM retrieval_units WHERE meeting_id=?',(ids[0],))

def test_build_cancel_and_config_change_guard(setup):
    c,store,r,_,pid=setup;configure(setup)
    jid=c.post('/api/retrieval/build',json={}).json()['id']
    assert c.post('/api/retrieval/build',json={}).status_code==409
    assert c.put('/api/retrieval/config',json={'profile_id':pid,'model':'embed2'}).status_code==409
    c.post(f'/api/jobs/{jid}/cancel')
    with pytest.raises(Cancelled):c.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert r.status()['indexed']==0

def test_input_validation_and_sql_literal_query(setup):
    c,*_=setup
    assert c.post('/api/retrieval/search',json={'query':' '}).status_code==422
    assert c.post('/api/retrieval/search',json={'query':"' OR 1=1 --"}).status_code==200
    assert c.post('/api/retrieval/search',json={'query':'a','kind':'invalid'}).status_code==422

@pytest.mark.parametrize('rows',[
    [{'index':0,'embedding':[0,0]}], [{'index':0,'embedding':[float('nan'),1]}],
    [{'index':0,'embedding':[True,1]}], [{'index':2,'embedding':[1,0]}],
    [{'index':0,'embedding':[1,0]},{'index':0,'embedding':[0,1]}],
])
def test_invalid_embedding_response_rejected(monkeypatch,rows):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return encode({'data':rows}).encode()
    class Opener:
        def open(self,*args,**kwargs):return Response()
    monkeypatch.setattr('app.retrieval.urllib.request.build_opener',lambda *_:Opener())
    with pytest.raises(ValueError):embeddings({'provider_type':'local','base_url':'http://127.0.0.1:1234/v1','model':'embed'},'embed',['x'])

def test_embedding_order_and_dimension(monkeypatch):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return encode({'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[2,0]}]}).encode()
    class Opener:
        def open(self,*args,**kwargs):return Response()
    monkeypatch.setattr('app.retrieval.urllib.request.build_opener',lambda *_:Opener())
    profile={'provider_type':'local','base_url':'http://127.0.0.1:1234/v1','model':'embed'}
    assert embeddings(profile,'embed',['a','b'],2)==[[1,0],[0,1]]
    with pytest.raises(ValueError):embeddings(profile,'embed',['a','b'],3)

def test_endpoint_change_cloud_config_and_model_busy(setup):
    c,store,r,ids,pid=setup;configure(setup);build(setup)
    c.app.state.worker.busy_jid='busy'
    assert c.post('/api/retrieval/search',json={'query':'联调','mode':'semantic'}).status_code==409
    assert c.post('/api/retrieval/search',json={'query':'联调','mode':'keyword'}).status_code==200
    c.app.state.worker.busy_jid=None
    store.execute("UPDATE llm_configs SET base_url='http://127.0.0.1:9999/v1' WHERE id=?",(pid,))
    assert '变化' in r.status()['error']
    assert c.post('/api/retrieval/search',json={'query':'联调','mode':'semantic'}).status_code==409
    store.execute("UPDATE llm_configs SET provider_type='cloud',base_url='https://example.com/v1' WHERE id=?",(pid,))
    assert c.put('/api/retrieval/config',json={'profile_id':pid,'model':'embed'}).status_code==409

def test_prefix_identity_and_index_resume(setup,monkeypatch):
    c,store,r,ids,pid=setup;seen=[]
    def fake(profile,model,texts,dimension=None):
        seen.extend(texts);return [[1,0] for _ in texts]
    monkeypatch.setattr('app.retrieval.embeddings',fake)
    config=configure(setup,'text-embedding-nomic-embed-text-v1.5')
    assert config['query_prefix']=='search_query: '
    build(setup);seen.clear()
    assert Retrieval(store).status()['indexed']==2
    build(setup);assert seen==[]
    c.post('/api/retrieval/search',json={'query':'联调','mode':'semantic'})
    assert seen==['search_query: 联调']
