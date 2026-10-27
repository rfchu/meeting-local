import json,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.analysis import LMStudioProvider,AIMinutesResult,AIMinutesBasicInfo,AnalysisItem

@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid();store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成会议','test.wav'))
        store.save_revision(mid,[{'id':'original-long-uuid','start':0,'end':5,'speaker':'甲','text':'合成议题周五交付。'}],'manual')
        yield client,store,mid

def result():return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='合成会议',source_segment_ids=['original-long-uuid']),core_content=[AnalysisItem(content='周五交付',source_segment_ids=['original-long-uuid'])],action_items=[],summary=[AnalysisItem(content='确认交付',source_segment_ids=['original-long-uuid'])])
def provider(monkeypatch,generate):
    class Fake:
        base_url='http://localhost:1234/v1'
        def resolve_model(self,*args):return 'synthetic'
        def generate_ai_minutes(self,*args):return generate()
    monkeypatch.setattr('app.main.LMStudioProvider',Fake)

def test_generation_failure_stays_visible_after_reload(setup,monkeypatch):
    client,store,mid=setup
    def fail():raise RuntimeError('模型输出被截断，请重新生成')
    provider(monkeypatch,fail)
    assert client.post(f'/api/meetings/{mid}/minutes/ai',json={}).status_code==409
    status=client.get(f'/api/meetings/{mid}').json()['minutes_generation']
    assert status['state']=='failed' and '截断' in status['error']
    with TestClient(create_app(store.root,start_worker=False)) as restarted:
        assert restarted.get(f'/api/meetings/{mid}').json()['minutes_generation']['id']==status['id']

def test_pending_status_prevents_duplicate_and_exposes_saved_artifact(setup,monkeypatch):
    client,store,mid=setup;entered=threading.Event();release=threading.Event()
    def generate():entered.set();assert release.wait(5);return result()
    provider(monkeypatch,generate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(lambda:client.post(f'/api/meetings/{mid}/minutes/ai',json={}))
        try:
            assert entered.wait(3)
            status=client.get(f'/api/meetings/{mid}').json()['minutes_generation']
            assert status['state']=='running' and status['elapsed_seconds']>=0
            assert client.post(f'/api/meetings/{mid}/minutes/ai',json={}).status_code==409
        finally:release.set()
        response=pending.result(timeout=5);assert response.status_code==201
    status=client.get(f'/api/meetings/{mid}').json()['minutes_generation']
    assert status['state']=='completed' and status['artifact_id']==response.json()['id']
    assert len(store.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'"))==1

def test_abandoned_request_does_not_leave_permanent_spinner(setup):
    client,store,mid=setup
    (store.directory(mid)/'minutes-generation.json').write_text(encode({'id':'old','state':'running','started_at':1,'revision':1,'error':''}))
    status=client.get(f'/api/meetings/{mid}').json()['minutes_generation']
    assert status['state']=='failed' and '中断' in status['error']

def test_compact_citations_map_back_and_invalid_ids_are_rejected(tmp_path):
    provider=LMStudioProvider();segments=[{'id':'real-uuid-'+str(i)*32,'start':i,'end':i+1,'speaker':'甲','text':'合成文字'} for i in range(719)]
    metadata={'reviewed_attention':[{'content':'重点','source_segment_ids':[segments[0]['id']]}]}
    def run(args,extra):
        request=json.loads(Path(args[args.index('--request')+1]).read_text())
        assert len(json.dumps(request['response_format']))<30000
        assert request['chat_template_kwargs']=={'enable_thinking':False}
        assert request['max_tokens']>=12288
        assert 'real-uuid' not in request['messages'][-1]['content']
        assert '/no_think' in request['messages'][-1]['content']
        response={'basic_info':{'topic':'合成会议','source_segment_ids':['s1']},'core_content':[{'content':'合成内容','source_segment_ids':['s1']}],'action_items':[],'summary':[]}
        Path(args[args.index('--output')+1]).write_text(encode(response))
    output=provider.generate_ai_minutes('qwen-synthetic',segments,metadata,tmp_path/'result',run)
    assert output.core_content[0].source_segment_ids==[segments[0]['id']]
    assert output.basic_info.source_segment_ids==[segments[0]['id']]
    def invalid(args,extra):
        Path(args[args.index('--output')+1]).write_text(encode({'basic_info':{'topic':'合成会议','source_segment_ids':['s99999']},'core_content':[],'action_items':[],'summary':[]}))
    with pytest.raises(ValueError,match='不存在'):provider.generate_ai_minutes('qwen-synthetic',segments,metadata,tmp_path/'invalid',invalid)


def test_native_minutes_disables_reasoning_without_tools_or_history(monkeypatch):
    from scripts import lmstudio_analyze as script
    seen=[]
    def send(url,headers,payload):
        seen.append((url,payload))
        return {'output':[{'type':'reasoning','content':'不应作为纪要'},{'type':'message','content':'{"ok":true}'}]}
    monkeypatch.setattr(script,'request_once',send)
    response=script.native_minutes_requester()('http://127.0.0.1:1234/v1/chat/completions',{}, {'model':'synthetic','messages':[{'role':'system','content':'JSON only'},{'role':'user','content':'合成请求'}],'max_tokens':12288})
    assert seen[0][0]=='http://127.0.0.1:1234/api/v1/chat'
    assert seen[0][1]['reasoning']=='off' and seen[0][1]['store'] is False and seen[0][1]['integrations']==[]
    assert response['choices'][0]['message']['content']=='{"ok":true}'


def test_unsupported_native_endpoint_falls_back_without_reprobing(monkeypatch):
    import urllib.error
    from scripts import lmstudio_analyze as script
    native=[];compatible=[]
    def send(*args):native.append(True);raise urllib.error.HTTPError('http://localhost',404,'unsupported',{},None)
    monkeypatch.setattr(script,'request_once',send)
    monkeypatch.setattr(script,'request_compatible',lambda *args:compatible.append(True) or {'ok':True})
    request=script.native_minutes_requester();payload={'model':'synthetic','messages':[]}
    for _ in range(2):assert request('http://localhost/v1/chat/completions',{},payload)=={'ok':True}
    assert len(native)==1 and len(compatible)==2



def test_unknown_speakers_do_not_produce_empty_enum_in_schema():
    schema=LMStudioProvider._ai_minutes_response_format({'s1'},set())['json_schema']['schema']
    attendees=schema['properties']['basic_info']['properties']['attendees']
    assert attendees['maxItems']==0 and 'enum' not in attendees['items']
