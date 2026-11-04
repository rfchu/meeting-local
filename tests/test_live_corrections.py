import json
import pytest
from fastapi.testclient import TestClient
from app.db import Store,encode
from app.recordings import RecordingStore
from app.live_results import LiveResults
from app.live_context import recognition_context
from app.live_corrections import LiveCorrections,validate_edits,apply_edits
from app.main import create_app

@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;recordings=RecordingStore(store);cache=LiveResults(store,recordings)
        rid=recordings.create('audio/webm')['id']
        def save(offset,text,confirmed=True):
            aid,_=cache.begin(rid,'browser','qwen',offset,15,str(offset).encode(),[])
            if confirmed:cache.complete(aid,[{'start':offset,'end':offset+15,'text':text}])
            else:cache.preview(aid)
            return aid
        aid=save(0,'请打开听季查看会议记录。')
        profile={'name':'local','provider_type':'local','base_url':'http://127.0.0.1:1234/v1','model':'fixture'}
        pid=client.post('/api/llm-configs',json=profile).json()['id']
        calls=[]
        def request(url,headers,payload,timeout):
            calls.append(payload)
            return {'choices':[{'finish_reason':'stop','message':{'content':encode({'edits':[{'original':'听季','replacement':'听记','reason':'结合前文产品名，建议核对。'}]})}}]}
        monkeypatch.setattr('app.live_corrections.request_once',request)
        yield client,store,recordings,cache,rid,aid,pid,save,calls

def test_suggestions_accept_undo_survive_and_raw_is_immutable(setup):
    client,store,recordings,cache,rid,aid,pid,save,calls=setup
    body={'range_id':aid,'segment_index':0,'profile_id':pid}
    url=f'/api/recordings/{rid}/corrections'
    response=client.post(url,json=body);assert response.status_code==200,response.text
    correction=response.json();assert correction['state']=='suggested'
    assert cache.snapshot(rid,'qwen')[0]['segments'][0]['text']=='请打开听季查看会议记录。'
    assert client.post(url,json=body).json()['id']==correction['id'] and len(calls)==1
    assert client.put(url+'/'+correction['id'],json={'state':'accepted'}).status_code==200
    snapshot=cache.snapshot(rid,'qwen')
    assert snapshot[0]['segments'][0]['text']=='请打开听记查看会议记录。'
    assert snapshot[0]['segments'][0]['asr_original_text']=='请打开听季查看会议记录。'
    assert json.loads(store.one('SELECT segments FROM live_asr_ranges WHERE id=?',(aid,))['segments'])[0]['text']=='请打开听季查看会议记录。'
    assert client.put(url+'/'+correction['id'],json={'state':'suggested'}).status_code==200
    assert cache.snapshot(rid,'qwen')[0]['segments'][0]['text']=='请打开听季查看会议记录。'
    assert snapshot[0]['segments'][0]['text']=='请打开听记查看会议记录。' # previously queued snapshot remains immutable
    assert LiveCorrections(Store(store.root)).list(rid)[0]['state']=='suggested'
    store.execute("INSERT INTO meetings(id,title,filename) VALUES(?,'published','audio.webm')",(rid,))
    assert client.put(url+'/'+correction['id'],json={'state':'accepted'}).status_code==409
    assert client.get(url).status_code==200 # audit still available after publication

def test_reject_preview_cross_session_and_cloud_before_contact(setup):
    client,store,recordings,cache,rid,aid,pid,save,calls=setup
    preview=save(15,'未确认',False)
    for recording,range_id in [(rid,preview),(recordings.create('audio/webm')['id'],aid)]:
        assert client.post(f'/api/recordings/{recording}/corrections',json={'range_id':range_id,'segment_index':0,'profile_id':pid}).status_code==409
    store.execute("UPDATE llm_configs SET provider_type='cloud' WHERE id=?",(pid,))
    assert client.post(f'/api/recordings/{rid}/corrections',json={'range_id':aid,'segment_index':0,'profile_id':pid}).status_code==409
    assert not calls

def test_failed_truncated_or_hallucinated_response_never_changes_asr(setup,monkeypatch):
    client,store,recordings,cache,rid,aid,pid,save,calls=setup
    for response in [ {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]},
        {'choices':[{'message':{'content':encode({'edits':[{'original':'不存在','replacement':'虚构','reason':'猜测'}]})}}]} ]:
        monkeypatch.setattr('app.live_corrections.request_once',lambda *a,**kw:response)
        assert client.post(f'/api/recordings/{rid}/corrections',json={'range_id':aid,'segment_index':0,'profile_id':pid}).status_code==409
        assert not LiveCorrections(store).list(rid)
        assert cache.overview(rid,'qwen',15)['covered_seconds']==15

def test_context_is_bounded_deduplicated_and_excludes_future_preview_and_other_session(setup):
    client,store,recordings,cache,rid,aid,pid,save,calls=setup
    session=recordings.read(rid);session['brief']={'title':'产品评审','participants':'小明','agenda':'背景'}
    recordings.write(recordings.folder(rid)/'session.json',session)
    save(0,'请打开听季查看会议记录。') # same interval not duplicated
    save(15,'未来内容');save(30,'未确认',False)
    context=recognition_context(cache,rid,15,'听记')
    assert '产品评审' in context and '听记' in context
    assert context.count('请打开听季查看会议记录。')==1
    assert '未来内容' not in context and '未确认' not in context
    assert recognition_context(cache,rid,15,'听记',False)==''
    other=recordings.create('audio/webm')['id']
    assert '请打开' not in recognition_context(cache,other,15)
    assert len(recognition_context(cache,rid,15,'词'*3000))<4000

@pytest.mark.parametrize('edits',[
    [{'original':'啊','replacement':'哦','reason':'重复'}],
    [{'original':'不存在','replacement':'词','reason':'臆测'}],
    [{'original':'你好啊','replacement':'您好','reason':'一'},{'original':'好啊','replacement':'好','reason':'二'}],
])
def test_invalid_edits_are_rejected(edits):
    with pytest.raises(ValueError):validate_edits({'edits':edits},'你好啊啊')

def test_valid_local_edits_apply_right_to_left():
    edits=[{'original':'听季','replacement':'听记','reason':'术语'},{'original':'模形','replacement':'模型','reason':'同音'}]
    text='听季使用模形转写'
    assert apply_edits(text,validate_edits({'edits':edits},text))=='听记使用模型转写'

@pytest.mark.parametrize('original,replacement',[('15万元','50万元'),('不同意','同意'),('三个人','两个人')])
def test_numbers_and_negation_are_never_changed_by_content_guess(original,replacement):
    with pytest.raises(ValueError,match='数量或否定'):
        validate_edits({'edits':[{'original':original,'replacement':replacement,'reason':'猜测'}]},original)

def test_deletion_during_llm_call_cannot_resurrect_transcript_content(setup,monkeypatch):
    client,store,recordings,cache,rid,aid,pid,save,calls=setup
    def request(*args,**kwargs):
        store.execute('DELETE FROM live_asr_ranges WHERE id=?',(aid,))
        return {'choices':[{'message':{'content':encode({'edits':[]})}}]}
    monkeypatch.setattr('app.live_corrections.request_once',request)
    assert client.post(f'/api/recordings/{rid}/corrections',json={'range_id':aid,'segment_index':0,'profile_id':pid}).status_code==409
    assert not LiveCorrections(store).list(rid)
