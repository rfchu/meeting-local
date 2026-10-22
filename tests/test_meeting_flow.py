import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.analysis import AIMinutesResult,AIMinutesBasicInfo,AnalysisItem
from app.minutes import MeetingMinutes,MinutesItem

@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as c:
        store=app.state.store;mid=uid();store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'交付会','sample.wav'))
        store.save_revision(mid,[{'id':'s1','start':0,'end':15,'speaker':'甲','text':'确定周五交付，负责人和期限仍需确认。'},{'id':'s2','start':1000,'end':1010,'speaker':'乙','text':'其他议题。'}],'manual')
        yield c,store,mid

def note(setup,text='我的私人判断，不能直接公开',scope='personal'):
    c,store,mid=setup;return c.post(f'/api/meetings/{mid}/notes',json={'raw_text':text,'captured_at':5,'note_type':'question','note_scope':scope}).json()['id']

def decide(setup,nid,decision='meeting',**patch):
    c,_,mid=setup;context=c.get(f'/api/meetings/{mid}/notes/{nid}/review').json()
    return c.put(f'/api/meetings/{mid}/notes/{nid}/disposition',json={'decision':decision,'content':'周五交付，负责人待确认','source_segment_ids':['s1'],'fingerprint':context['fingerprint'],'revision':context['revision'],'confirmed':True,**patch})

def artifact(setup):
    _,store,mid=setup;aid=uid();doc=MeetingMinutes(title='交付纪要',style='ai',action_items=[MinutesItem(content='核对交付负责人',source_segment_ids=['s1'])])
    store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(aid,mid,1,'meeting_minutes',encode(doc.model_dump()),encode({'edited':True})))
    return aid,doc

def fake_provider(monkeypatch,callback):
    class Provider:
        base_url='http://127.0.0.1:1234/v1'
        def resolve_model(self,model=None):return 'fake'
        def generate_ai_minutes(self,model,segments,metadata,work,run):
            callback(metadata)
            return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='交付会',source_segment_ids=['s1']),core_content=[AnalysisItem(content='周五交付，负责人待确认',source_segment_ids=['s1'])],action_items=[AnalysisItem(content='核对交付负责人',source_segment_ids=['s1'])],summary=[AnalysisItem(content='继续核对交付安排',source_segment_ids=['s1'])])
    monkeypatch.setattr('app.main.LMStudioProvider',Provider)

def test_preparation_round_trip_and_recording_binding(setup):
    c,store,mid=setup;brief={'title':'计划中的会议','purpose':'确认交付','agenda':'交付安排','questions':'负责人是谁','previous_meeting_id':mid}
    saved=c.put('/api/meeting-preparation',json=brief);assert saved.status_code==200
    assert c.get('/api/meeting-preparation').json()['title']==brief['title']
    rid=c.post('/api/recordings',json={}).json()['id']
    assert c.put(f'/api/recordings/{rid}/metadata',json={'notes':[],'duration':2,'chunk_count':1,'brief':saved.json()}).status_code==200
    # Old clients without brief must preserve it, rather than erase preparation on retry.
    c.put(f'/api/recordings/{rid}/metadata',json={'notes':[],'duration':2,'chunk_count':1})
    c.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'audio','application/octet-stream')})
    result=c.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1});assert result.status_code==200
    row=result.json();assert row['title']==brief['title'] and row['flow']['brief']['purpose']=='确认交付'
    assert row['flow']['previous']['id']==mid
    assert c.get('/api/meeting-preparation').json()['title']==''
    assert c.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1}).status_code==200

def test_preparation_finish_does_not_erase_newer_draft(setup):
    c,store,mid=setup
    from app.meeting_flow import publish_preparation
    c.put('/api/meeting-preparation',json={'title':'下一场会议'})
    with store.connect() as db:publish_preparation(db,mid,{'title':'旧会话草稿'})
    assert c.get('/api/meeting-preparation').json()['title']=='下一场会议'
    assert c.put(f'/api/meetings/{mid}/brief',json={'previous_meeting_id':mid}).status_code==409
    assert c.put(f'/api/meetings/{mid}/brief',json={'previous_meeting_id':'missing'}).status_code==409

def test_disposition_requires_confirmation_current_nearby_evidence(setup):
    nid=note(setup)
    for patch in ({'confirmed':False},{'source_segment_ids':[]},{'source_segment_ids':['s2']},{'source_segment_ids':['missing']},{'content':''},{'revision':0},{'fingerprint':'a'*64}):assert decide(setup,nid,**patch).status_code==409
    assert decide(setup,nid).status_code==200
    c,store,mid=setup
    assert store.one('SELECT note_scope FROM meeting_notes WHERE id=?',(nid,))['note_scope']=='personal'
    flow=c.get(f'/api/meetings/{mid}').json()['flow'];assert flow['counts']['meeting']==1
    assert decide(setup,nid,'personal').json()['counts']['personal']==1
    assert decide(setup,nid,'deferred').json()['counts']['deferred']==1

def test_personal_and_deferred_never_enter_ai_context(setup,monkeypatch):
    c,store,mid=setup;approved=note(setup);private=note(setup,'PRIVATE-SECRET');deferred=note(setup,'DEFERRED-SECRET')
    decide(setup,approved);decide(setup,deferred,'deferred')
    c.put(f'/api/meetings/{mid}/brief',json={'purpose':'会前计划，不是会议结论'})
    seen=[];fake_provider(monkeypatch,lambda metadata:seen.append(metadata))
    result=c.post(f'/api/meetings/{mid}/minutes/ai',json={});assert result.status_code==201
    context=seen[0];assert context['preparation']['purpose'].startswith('会前计划')
    assert [x['note_id'] for x in context['reviewed_attention']]==[approved]
    assert 'PRIVATE-SECRET' not in encode(context) and 'DEFERRED-SECRET' not in encode(context) and '我的私人判断' not in encode(context)
    flow=c.get(f'/api/meetings/{mid}').json()['flow'];assert flow['counts']['related']==1 and flow['counts']['adopted']==0
    saved=c.put(f'/api/meetings/{mid}/minutes',json={'source_minutes_id':result.json()['id'],'document':result.json()['payload']})
    assert saved.status_code==201 and saved.json()['provenance']['flow_context_hash']==result.json()['provenance']['flow_context_hash']
    decide(setup,approved,'personal')
    detail=c.get(f'/api/meetings/{mid}').json();assert detail['artifacts'][0]['stale'] and not detail['flow']['has_current_minutes']

def test_generation_refuses_changed_attention_or_preparation(setup,monkeypatch):
    c,store,mid=setup;nid=note(setup);decide(setup,nid)
    def change(metadata):c.put(f'/api/meetings/{mid}/brief',json={'purpose':'生成期间修改'})
    fake_provider(monkeypatch,change)
    assert c.post(f'/api/meetings/{mid}/minutes/ai',json={}).status_code==409
    assert not store.all("SELECT id FROM artifacts WHERE meeting_id=? AND kind='meeting_minutes'",(mid,))

def test_reviewed_private_note_can_be_adopted_without_exposing_raw(setup):
    c,store,mid=setup;nid=note(setup);decide(setup,nid);aid,_=artifact(setup)
    context=c.get(f'/api/meetings/{mid}/notes/{nid}/review').json()
    body={**context,'kind':'minutes','content':'周五交付，负责人待确认','source_segment_ids':['s1'],'section':'conclusions','minutes_id':aid,'confirmed':True};body.pop('segments')
    result=c.post(f'/api/meetings/{mid}/notes/{nid}/use',json=body);assert result.status_code==200
    detail=c.get(f'/api/meetings/{mid}').json();assert detail['flow']['counts']['adopted']==1
    hits=c.post('/api/retrieval/search',json={'query':'周五'}).json()['results'];assert any(h['kind']=='expanded_note' and h['note_scope']=='meeting' for h in hits)
    decide(setup,nid,'personal')
    detail=c.get(f'/api/meetings/{mid}').json();assert detail['artifacts'][0]['stale']
    assert c.post(f'/api/meetings/{mid}/notes/{nid}/use',json=body).status_code==409
    assert not any(h['kind']=='expanded_note' for h in c.post('/api/retrieval/search',json={'query':'周五'}).json()['results'])

def test_action_track_deduplication_source_stale_and_global_followup(setup):
    c,store,mid=setup;aid,doc=artifact(setup)
    path=f'/api/meetings/{mid}/minutes/{aid}/actions/0/track';body={'content':'核对交付负责人','confirmed':True,'owner':'李工','due_date':''}
    assert c.post(path,json={**body,'confirmed':False}).status_code==409
    first=c.post(path,json=body);assert first.status_code==201 and not first.json()['reused']
    assert c.post(path,json=body).json()['reused']
    rows=c.get('/api/followups').json();assert len(rows)==1 and rows[0]['source_kind']=='minutes' and rows[0]['owner']=='李工' and not rows[0]['stale']
    tid=rows[0]['id'];assert c.put(f'/api/meetings/{mid}/note-todos/{tid}',json={'status':'done','owner':'李工','due_date':''}).status_code==200
    assert c.get('/api/followups').json()[0]['status']=='done'
    store.save_revision(mid,[{'id':'s3','start':0,'end':1,'speaker':'甲','text':'安排变化'}],'manual',expected=1)
    assert c.get('/api/followups').json()[0]['stale']
    assert c.post(path,json=body).status_code==409

def test_disposition_deleted_and_raw_change_invalidates_approval(setup):
    c,store,mid=setup;nid=note(setup,scope='meeting');decide(setup,nid)
    c.put(f'/api/meetings/{mid}/notes/{nid}',json={'raw_text':'新想法','note_type':'question'})
    assert c.get(f'/api/meetings/{mid}').json()['flow']['counts']['stale']==1
    decide(setup,nid,'personal')
    assert store.one('SELECT note_scope FROM meeting_notes WHERE id=?',(nid,))['note_scope']=='personal'
    assert c.delete(f'/api/meetings/{mid}/notes/{nid}').status_code==200
    assert not store.one('SELECT * FROM attention_reviews WHERE note_id=?',(nid,))
    c.put(f'/api/meetings/{mid}/brief',json={'purpose':'test'})
    assert c.delete(f'/api/meetings/{mid}').status_code==200
    assert not store.one('SELECT * FROM meeting_briefs WHERE meeting_id=?',(mid,))

def test_native_recording_retains_preparation_and_markers(setup):
    from app.recordings import RecordingStore
    c,store,mid=setup;r=RecordingStore(store);session=r.create('audio/wav');rid=session['id'];folder=r.folder(rid)
    session['source']='macos';r.write(folder/'session.json',session)
    (folder/'system.pcm').write_bytes(b'\0\0'*16000)
    r.write(folder/'capture.json',{'state':'stopped','duration':1,'error':''})
    body={'notes':[{'captured_at':.5,'raw_text':'','is_marker':True,'note_type':'action'}],'duration':1,'chunk_count':0,'brief':{'title':'原生准备会','purpose':'测试准备随录音保留'}}
    assert c.put(f'/api/recordings/{rid}/metadata',json=body).status_code==200
    result=c.post(f'/api/native-capture/{rid}/finish');assert result.status_code==200,result.text
    assert result.json()['title']=='原生准备会' and result.json()['flow']['brief']['purpose']==body['brief']['purpose']
    assert result.json()['notes'][0]['is_marker'] and result.json()['notes'][0]['note_type']=='action'

def test_reviewed_task_turning_personal_invalidates_source_without_deleting_task(setup):
    c,store,mid=setup;nid=note(setup);decide(setup,nid)
    context=c.get(f'/api/meetings/{mid}/notes/{nid}/review').json();context.pop('segments')
    created=c.post(f'/api/meetings/{mid}/notes/{nid}/use',json={**context,'kind':'todo','content':'周五交付，负责人待确认','source_segment_ids':['s1'],'confirmed':True});assert created.status_code==200
    first=c.get('/api/followups').json()[0];assert first['note_scope']=='meeting' and not first['stale']
    decide(setup,nid,'personal');rows=c.get('/api/followups').json();assert len(rows)==1 and rows[0]['stale']

def test_stale_generated_minutes_cannot_be_polished_and_stale_save_is_rejected(setup,monkeypatch):
    c,store,mid=setup;nid=note(setup);decide(setup,nid);fake_provider(monkeypatch,lambda metadata:None)
    first=c.post(f'/api/meetings/{mid}/minutes/ai',json={}).json()
    second=c.put(f'/api/meetings/{mid}/minutes',json={'source_minutes_id':first['id'],'document':first['payload']});assert second.status_code==201
    assert c.put(f'/api/meetings/{mid}/minutes',json={'source_minutes_id':first['id'],'document':first['payload']}).status_code==409
    decide(setup,nid,'personal')
    assert c.post(f'/api/meetings/{mid}/minutes/polish',json={}).status_code==409
    checkpoint=c.get(f'/api/meetings/{mid}').json()['review']['steps']['minutes'];assert checkpoint['state']=='blocked'
