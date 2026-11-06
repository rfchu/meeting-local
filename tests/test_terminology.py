import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.terminology import Candidates,validate_candidates,recognition_terms,search_forms,process_candidates
from app.meeting_flow import ai_context
from app.retrieval import Retrieval

@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid()
        store.directory(mid).mkdir(exist_ok=True)
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'术语测试','test.wav'))
        store.save_revision(mid,[{'id':'s1','start':0,'end':3,'text':'请使用库伯内特斯部署。','speaker':'甲'}],'manual',expected=0)
        yield client,store,mid

def add(client,scope,name='Kubernetes',aliases=None,**extra):
    return client.post(f'/api/terminology/{scope}',json={'name':name,'aliases':aliases or ['库伯内特斯'],**extra})

def candidate():
    return {'terms':[{'name':'Kubernetes','category':'technical','aliases':['库伯内特斯'],'reason':'可能是技术名称，请回听确认',
        'evidence':[{'segment_id':'s1','quote':'库伯内特斯'}]}]}

def seed_candidate(store,mid,revision=1):
    tid=uid();item=candidate()['terms'][0]
    store.execute("INSERT INTO terminology(id,scope,name,category,aliases,status,source,evidence,revision,reason) VALUES(?,?,?,'technical',?,'candidate','ai',?,?,?)",
        (tid,mid,item['name'],encode(item['aliases']),encode(item['evidence']),revision,item['reason']))
    return tid

def test_crud_scope_validation_normalization_and_optimistic_lock(setup):
    client,store,mid=setup
    assert add(client,'missing').status_code==404
    assert add(client,mid,name='   ').status_code==422
    term=add(client,mid,name=' Kubernetes ',aliases=['库伯内特斯','库伯内特斯','Kubernetes','']).json()
    assert term['aliases']==['库伯内特斯'] and term['status']=='confirmed'
    assert add(client,mid,name='kubernetes').status_code==409
    url=f"/api/terminology/{mid}/{term['id']}"
    body={**term,'name':'K8s'}
    saved=client.put(url,json=body);assert saved.status_code==200 and saved.json()['version']==2
    assert client.put(url,json=body).status_code==409
    assert client.delete(url+'?version=1').status_code==409
    assert client.delete(url+'?version=2').status_code==200
    assert not client.get(f'/api/terminology/{mid}').json()['terms']
    from app.migrations import LATEST_VERSION
    assert store.one('PRAGMA user_version')['user_version']==LATEST_VERSION

def test_candidate_never_primes_asr_or_search_until_confirmed(setup):
    client,store,mid=setup;tid=seed_candidate(store,mid)
    assert recognition_terms(store,mid)==''
    listing=client.get(f'/api/terminology/{mid}').json()['terms']
    assert search_forms('Kubernetes',listing)==['kubernetes']
    term=listing[0]
    assert client.put(f'/api/terminology/{mid}/{tid}',json={**term,'status':'confirmed'}).status_code==200
    assert recognition_terms(store,mid)=='Kubernetes'
    assert '库伯内特斯' not in recognition_terms(store,mid)
    assert '库伯内特斯' in search_forms('Kubernetes',client.get(f'/api/terminology/{mid}').json()['terms'])
    assert json.loads(store.one('SELECT segments FROM meetings WHERE id=?',(mid,))['segments'])[0]['text']=='请使用库伯内特斯部署。'

def test_stale_evidence_cannot_be_adopted_and_wrong_scope_is_rejected(setup):
    client,store,mid=setup;tid=seed_candidate(store,mid)
    segments=json.loads(store.one('SELECT segments FROM meetings WHERE id=?',(mid,))['segments'])
    store.save_revision(mid,segments,'manual',expected=1)
    term=client.get(f'/api/terminology/{mid}').json()['terms'][0]
    assert term['stale']
    assert client.put(f'/api/terminology/{mid}/{tid}',json={**term,'status':'confirmed'}).status_code==409
    assert client.put(f'/api/terminology/library/{tid}',json={**term,'status':'confirmed'}).status_code==404
    assert client.put(f'/api/terminology/{mid}/{tid}',json={**term,'status':'dismissed'}).status_code==200

def test_personal_library_local_hints_but_not_implicitly_sent_to_minutes(setup):
    client,store,mid=setup
    with store.connect() as db:original=ai_context(db,mid)[1]
    assert add(client,'library').status_code==201
    assert recognition_terms(store,mid)=='Kubernetes'
    with store.connect() as db:
        context,key=ai_context(db,mid)
    assert key==original and 'confirmed_terminology' not in context
    assert add(client,mid).status_code==201
    with store.connect() as db:
        context,key=ai_context(db,mid)
    assert key!=original and context['confirmed_terminology'][0]['name']=='Kubernetes'
    assert len(client.get(f'/api/terminology/{mid}').json()['library'])==1

def test_scoped_alias_search_preserves_original_content(setup):
    client,store,mid=setup
    other=uid();store.directory(other).mkdir(exist_ok=True)
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(other,'其他会议','test.wav'))
    store.save_revision(other,[{'id':'s2','start':0,'end':2,'text':'库伯内特斯测试。','speaker':'乙'}],'manual',expected=0)
    assert add(client,mid).status_code==201
    results=Retrieval(store).search('Kubernetes')
    assert len(results['results'])==1 and results['results'][0]['meeting_id']==mid
    assert '库伯内特斯' in results['results'][0]['content']
    assert add(client,'library').status_code==201
    assert len(Retrieval(store).search('Kubernetes')['results'])==2

def test_recording_scope_survives_publication(setup):
    client,store,mid=setup
    session=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json();rid=session['id']
    assert add(client,rid).status_code==201
    assert 'Kubernetes' in recognition_terms(store,rid)
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(rid,'已完成','x.wav'))
    assert len(client.get(f'/api/terminology/{rid}').json()['terms'])==1

def test_candidate_evidence_and_aliases_must_be_exact():
    source=[{'id':'s1','text':'库伯内特斯部署'}]
    assert validate_candidates(Candidates.model_validate(candidate()),source).terms
    for change in [ {'evidence':[{'segment_id':'missing','quote':'库伯内特斯'}]},
        {'evidence':[{'segment_id':'s1','quote':'不存在'}]}, {'aliases':['虚构名称']}, {'aliases':[]} ]:
        payload=candidate();payload['terms'][0].update(change)
        with pytest.raises(ValueError):validate_candidates(Candidates.model_validate(payload),source)

def local_profile(store):
    pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,?,?,?)',(pid,'本地测试','local','http://127.0.0.1:1234/v1','test'))
    return pid

def fake_runner(store,payload):
    class Runner:
        def __init__(self):self.store=store;self.calls=0
        def update(self,*_):pass
        def run(self,jid,args,*a,**kw):
            from pathlib import Path
            self.calls+=1;Path(args[args.index('--output')+1]).write_text(encode(payload))
    return Runner()

def queued(client,store,mid,pid):
    result=client.post(f'/api/terminology/{mid}/scan',json={'profile_id':pid})
    assert result.status_code==201,result.text
    jid=result.json()['id'];store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    return store.one('SELECT * FROM jobs WHERE id=?',(jid,))

def test_queued_scan_validates_before_publish_and_refreshes_stale_candidates(setup):
    client,store,mid=setup;pid=local_profile(store);tid=seed_candidate(store,mid,revision=0)
    runner=fake_runner(store,candidate());job=queued(client,store,mid,pid)
    process_candidates(runner,job)
    row=store.one('SELECT * FROM terminology WHERE id=?',(tid,))
    assert row['revision']==1 and row['status']=='candidate' and row['version']==2
    assert runner.calls==1
    # Re-running a completed chunk after an interrupted publication uses validated checkpoint.
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(job['id'],))
    process_candidates(runner,job);assert runner.calls==1
    term=client.get(f'/api/terminology/{mid}').json()['terms'][0]
    assert client.put(f'/api/terminology/{mid}/{tid}',json={**term,'status':'confirmed','name':'Kubernetes 已核对'}).status_code==200
    process_candidates(runner,queued(client,store,mid,pid))
    assert store.one('SELECT name FROM terminology WHERE id=?',(tid,))['name']=='Kubernetes 已核对'

def test_scan_blocks_cancelled_or_changed_transcript_publication(setup):
    client,store,mid=setup;pid=local_profile(store);job=queued(client,store,mid,pid)
    segments=json.loads(store.one('SELECT segments FROM meetings WHERE id=?',(mid,))['segments'])
    store.save_revision(mid,segments,'manual',expected=1)
    with pytest.raises(ValueError,match='文字稿已变化'):process_candidates(fake_runner(store,candidate()),job)
    assert not store.one('SELECT id FROM terminology WHERE scope=?',(mid,))
    store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job['id'],))
    job=queued(client,store,mid,pid);store.execute("UPDATE jobs SET state='cancelled' WHERE id=?",(job['id'],))
    with pytest.raises(ValueError,match='取消'):process_candidates(fake_runner(store,candidate()),job)
    assert not store.one('SELECT id FROM terminology WHERE scope=?',(mid,))

def test_cloud_scan_consent_binds_endpoint_and_model(setup):
    client,store,mid=setup;pid=uid()
    store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,?,?,?,?)',(pid,'云端','cloud','https://example.com/v1','test','secret'))
    url=f'/api/terminology/{mid}/scan'
    assert client.post(url,json={'profile_id':pid,'confirm_cloud':True}).status_code==409
    expected={'id':pid,'provider_type':'cloud','base_url':'https://old.example.com/v1','model':'test'}
    assert client.post(url,json={'profile_id':pid,'confirm_cloud':True,'expected_provider':expected}).status_code==409
    expected['base_url']='https://example.com/v1'
    assert client.post(url,json={'profile_id':pid,'confirm_cloud':True,'expected_provider':expected}).status_code==201
    assert 'secret' not in store.one('SELECT config FROM jobs WHERE meeting_id=?',(mid,))['config']

def test_model_configuration_changes_block_dispatch(setup):
    client,store,mid=setup;pid=local_profile(store);job=queued(client,store,mid,pid)
    store.execute('UPDATE llm_configs SET base_url=? WHERE id=?',('http://127.0.0.1:1235/v1',pid))
    with pytest.raises(ValueError,match='配置已变化'):process_candidates(fake_runner(store,candidate()),job)

def test_meeting_deletion_removes_only_its_terminology(setup):
    client,store,mid=setup
    add(client,mid);add(client,'library')
    assert client.delete(f'/api/meetings/{mid}').status_code==200
    assert store.one('SELECT count(*) AS n FROM terminology')['n']==1
    assert store.one('SELECT scope FROM terminology')['scope']=='library'

def test_llm_script_accepts_term_schema_and_rejects_wrong_shape():
    from scripts.lmstudio_analyze import validate_shape
    assert validate_shape(candidate(),'meeting_terms')==candidate()
    with pytest.raises(ValueError):validate_shape({'summary':[]},'meeting_terms')


def test_term_json_retries_stay_within_reserved_output_budget():
    from scripts.lmstudio_analyze import complete_json
    calls=[]
    def request(url,headers,payload):
        calls.append(payload)
        return {'choices':[{'message':{'content': '{"terms":[' if len(calls)==1 else '{"terms":[]}'},'finish_reason':'length' if len(calls)==1 else 'stop'}]}
    payload={'model':'test','max_tokens':4096,'messages':[],
        'response_format':{'type':'json_schema','json_schema':{'name':'meeting_terms'}}}
    assert complete_json('http://localhost',{},payload,requester=request)=={'terms':[]}
    assert [c['max_tokens'] for c in calls]==[4096,4096]

def test_confirmed_terms_feed_actual_asr_context_only_when_enabled(setup):
    from types import SimpleNamespace
    from app.live_context import recognition_context
    from app.recordings import RecordingStore
    client,store,mid=setup
    rid=client.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
    add(client,rid);add(client,'library',name='MLX',aliases=['麦乐克斯'])
    cache=SimpleNamespace(store=store,recordings=RecordingStore(store))
    context=recognition_context(cache,rid,0)
    assert 'Kubernetes' in context and 'MLX' in context
    assert '库伯内特斯' not in context and '麦乐克斯' not in context
    assert recognition_context(cache,rid,0,enabled=False)==''
