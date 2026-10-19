import json,sys
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.db import uid,encode
from app.main import create_app
from app.qa import QA,Answer,validate_answer,lexical_query
from app.worker import Cancelled

@pytest.fixture
def data(tmp_path):
    application=create_app(tmp_path,start_worker=False)
    with TestClient(application) as client:
        store=application.state.store;pid=uid();mids=[]
        store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,context_window) VALUES(?,?,?,?,?,?)',
                      (pid,'本地验收','local','http://127.0.0.1:1234/v1','fixture',32768))
        for title,text in [('排期讨论','项目约定周五交付，负责人待确认。'),('排期调整','项目交付改为下周一，仍需确认负责人。')]:
            mid=uid();mids.append(mid)
            store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,title,'fixture.wav',20))
            store.save_revision(mid,[{'id':'seg','start':0,'end':10,'speaker':'发言人 1','text':text,'boundary_review':False}],'manual')
        yield client,store,QA(store),pid,mids

def prepare(data,**kwargs):
    client,_,_,pid,_=data
    result=client.post('/api/retrieval/qa/prepare',json={'question':'项目的交付时间如何变化？','profile_id':pid,'query':'项目',**kwargs})
    assert result.status_code==201,result.text
    return result.json()

def process(data,run,monkeypatch,change=None):
    client,store,qa,_,_=data
    def fake(provider,model,question,sources,work,execute):
        if change:change()
        return Answer(status='answered',claims=[{'text':'本次检索记录了交付安排。','kind':'record',
            'citations':[{'source':sources[0]['label'],'quote':sources[0]['content']}]}])
    monkeypatch.setattr('app.qa.generate_answer',fake)
    queued=qa.enqueue(run['id']);jid=queued['job']['id']
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    store.execute("UPDATE jobs SET state='completed',stage='引用问答完成',progress=1 WHERE id=?",(jid,))
    return qa.read(run['id'])

def test_preview_is_model_free_and_answer_is_bound_to_multiple_meetings(data,monkeypatch):
    spy=Mock(side_effect=AssertionError('preview must not call LLM'));monkeypatch.setattr('app.qa.generate_answer',spy)
    run=prepare(data)
    assert len({s['meeting_id'] for s in run['sources']})==2
    assert not run['answer'] and run['state']=='prepared'
    assert run['evidence_chars']<=run['context_budget'];spy.assert_not_called()
    result=process(data,run,monkeypatch)
    assert result['answer']['status']=='answered'
    assert result['job']['state']=='completed'
    assert not list((data[1].root/'qa-work').glob('*'))

@pytest.mark.parametrize('mutation', ['missing_id','fake_quote','uncited','note_as_fact','insufficient_with_claims'])
def test_unverifiable_answers_are_rejected(mutation):
    sources=[{'label':'E1','content':'这是个人判断，不是决定。','kind':'note','note_scope':'personal'}]
    value={'status':'answered','claims':[{'text':'判断','kind':'note','citations':[{'source':'E1','quote':'个人判断'}]}]}
    if mutation=='missing_id':value['claims'][0]['citations'][0]['source']='E999'
    if mutation=='fake_quote':value['claims'][0]['citations'][0]['quote']='全体已经决定'
    if mutation=='uncited':value['claims'][0]['citations']=[]
    if mutation=='note_as_fact':value['claims'][0]['kind']='record'
    if mutation=='insufficient_with_claims':value['status']='insufficient'
    with pytest.raises(ValueError):validate_answer(Answer.model_validate(value),sources)

def test_insufficient_result_has_no_invented_summary():
    assert validate_answer(Answer(status='insufficient',claims=[]),[]).claims==[]

def test_personal_notes_excluded_before_ranking_and_opt_in_labeled(data):
    client,store,qa,pid,mids=data
    for _ in range(65):
        client.post(f'/api/meetings/{mids[0]}/notes',json={'raw_text':'项目 项目 个人预测','captured_at':2})
    run=prepare(data,kind=None)
    assert run['sources'] and all(s.get('note_scope')!='personal' for s in run['sources'])
    included=prepare(data,kind=None,include_personal=True)
    assert any(s['note_scope']=='personal' for s in included['sources'])

def test_cloud_requires_confirmation_and_binds_endpoint(data):
    client,store,qa,pid,_=data
    store.execute("UPDATE llm_configs SET provider_type='cloud',base_url='https://example.com/v1',api_key='test-secret' WHERE id=?",(pid,))
    run=prepare(data)
    url=f"/api/retrieval/qa/{run['id']}/generate"
    assert client.post(url,json={}).status_code==409
    assert 'test-secret' not in json.dumps(run)
    store.execute("UPDATE llm_configs SET base_url='https://other.example.com/v1' WHERE id=?",(pid,))
    assert client.post(url,json={'confirm_cloud':True}).status_code==409
    fresh=prepare(data)
    assert client.post(f"/api/retrieval/qa/{fresh['id']}/generate",json={'confirm_cloud':True}).status_code==201

def test_changed_source_before_generation_and_during_generation_never_publishes(data,monkeypatch):
    _,store,qa,_,mids=data
    run=prepare(data)
    store.execute('UPDATE meetings SET title=? WHERE id=?',('改名',mids[0]))
    with pytest.raises(ValueError):qa.enqueue(run['id'])
    assert qa.read(run['id'])['stale'] and not qa.read(run['id'])['sources']
    run=prepare(data)
    with pytest.raises(ValueError,match='生成期间'):
        process(data,run,monkeypatch,lambda:store.execute('UPDATE meetings SET title=? WHERE id=?',('再次改名',mids[0])))
    assert not qa.row(run['id'])['answer']

def test_historical_answers_hidden_on_revision_change_and_removed_with_meeting(data,monkeypatch):
    client,store,qa,_,mids=data;run=prepare(data);result=process(data,run,monkeypatch)
    source=result['sources'][0];mid=source['meeting_id']
    store.save_revision(mid,[{'id':'seg','start':0,'end':10,'speaker':'发言人 1','text':'新内容'}],'manual')
    assert qa.read(run['id'])['stale'] and qa.read(run['id'])['answer'] is None
    assert client.delete('/api/meetings/'+mid).status_code==200
    assert client.get('/api/retrieval/qa/'+run['id']).status_code==404
    assert not store.all('SELECT * FROM qa_runs WHERE id=?',(run['id'],))

def test_empty_evidence_never_enqueues_and_cancel_does_not_run_model(data,monkeypatch):
    client,store,qa,_,_=data
    run=prepare(data,query='zz_no_match_800')
    assert not run['sources']
    assert client.post(f"/api/retrieval/qa/{run['id']}/generate",json={}).status_code==409
    run=prepare(data);queued=qa.enqueue(run['id']);jid=queued['job']['id']
    client.post('/api/jobs/'+jid+'/cancel')
    spy=Mock(side_effect=AssertionError('cancel must not call model'));monkeypatch.setattr('app.qa.generate_answer',spy)
    with pytest.raises(Cancelled):client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    spy.assert_not_called();assert qa.read(run['id'])['answer'] is None
    assert client.delete('/api/retrieval/qa/'+run['id']).status_code==200

def test_worker_hung_child_is_terminated_at_deadline(data,monkeypatch,tmp_path):
    client,store,qa,_,_=data;run=prepare(data);jid=qa.enqueue(run['id'])['job']['id']
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    import subprocess
    original=subprocess.Popen;children=[]
    def popen(*args,**kwargs):
        child=original(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr('app.worker.subprocess.Popen',popen)
    with pytest.raises(RuntimeError,match='处理时限'):
        client.app.state.worker.run(jid,[sys.executable,'-c','import time;time.sleep(10)'],tmp_path/'child.log',timeout=.01)
    assert children[0].poll() is not None

def test_question_terms_are_bounded():
    query=lexical_query('项目交付时间是谁在什么时候决定的？')
    assert '交付' in query and len(query)<=500

def test_queued_provider_change_and_mid_generation_cancel_do_not_publish(data,monkeypatch):
    client,store,qa,pid,_=data
    run=prepare(data);jid=qa.enqueue(run['id'])['job']['id']
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    store.execute("UPDATE llm_configs SET model='other' WHERE id=?",(pid,))
    spy=Mock();monkeypatch.setattr('app.qa.generate_answer',spy)
    with pytest.raises(ValueError,match='模型配置变化'):client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    spy.assert_not_called()
    client.post('/api/jobs/'+jid+'/cancel')
    run=prepare(data)
    def cancel():
        current=qa.row(run['id'])['job_id'];client.post('/api/jobs/'+current+'/cancel')
    with pytest.raises(Cancelled):process(data,run,monkeypatch,cancel)
    assert not qa.row(run['id'])['answer']

def test_changed_adopted_note_cannot_remain_a_retrieval_fact(data):
    from app.notes import fingerprint
    client,store,qa,_,mids=data;mid=mids[0]
    nid=client.post(f'/api/meetings/{mid}/notes',json={'raw_text':'项目交付线索','captured_at':2,'note_scope':'meeting'}).json()['id']
    note=store.one('SELECT * FROM meeting_notes WHERE id=?',(nid,))
    store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
        (uid(),mid,1,'meeting_minutes',encode({'decisions':[{'content':'项目交付安排','source_segment_ids':['seg']}]}),
         encode({'edited':True,'adopted_notes':[{'note_id':nid,'note_fingerprint':fingerprint(note)}]})))
    run=prepare(data,kind='minutes');assert run['sources']
    response=client.put(f'/api/meetings/{mid}/notes/{nid}',json={'raw_text':'项目个人猜测','captured_at':2,'note_type':'note','note_scope':'personal'})
    assert response.status_code==200
    assert qa.read(run['id'])['stale']
    assert not prepare(data,kind='minutes')['sources']

def test_prompt_transport_keeps_secret_out_of_request_and_caps_retry_tokens(data,monkeypatch,tmp_path):
    from app.qa import generate_answer
    from app.analysis import OpenAICompatibleProvider
    from scripts.lmstudio_analyze import complete_json,NoRedirect
    run=prepare(data);profile=data[1].one('SELECT * FROM llm_configs WHERE id=?',(data[3],));profile['api_key']='private-token'
    def execute(args,extra):
        assert extra=={'MEETING_LLM_API_KEY':'private-token'}
        request=json.loads((tmp_path/'answer.request.json').read_text())
        assert 'private-token' not in encode(request)
        assert request['response_format']['json_schema']['name']=='retrieval_answer'
        (tmp_path/'answer.response.json').write_text(encode({'status':'insufficient','claims':[]}))
    generate_answer(OpenAICompatibleProvider(profile),profile['model'],'问题',run['sources'],tmp_path/'answer',execute)
    payload={'model':'fixture','max_tokens':2048,'messages':[],'response_format':{'type':'json_schema','json_schema':{'name':'retrieval_answer'}}}
    responses=[{'choices':[{'message':{'content':'broken'}}]},{'choices':[{'message':{'content':'{"status":"insufficient","claims":[]}'}}]}]
    def fake(url,headers,body):assert body['max_tokens']==2048;return responses.pop(0)
    monkeypatch.setattr('scripts.lmstudio_analyze.request_compatible',fake)
    assert complete_json('http://localhost',{},payload)['status']=='insufficient'
    with pytest.raises(RuntimeError,match='不允许重定向'):NoRedirect().redirect_request(None,None,None,None,None,None)
