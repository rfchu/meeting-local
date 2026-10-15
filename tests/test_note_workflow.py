import io,json,wave
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.minutes import MeetingMinutes

@pytest.fixture
def setup(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        s=c.app.state.store;mid=uid();s.directory(mid).mkdir(exist_ok=True)
        s.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,'项目评审','sample.wav',120))
        segments=[{'id':'a','start':10,'end':20,'text':'李工在周五完成接口联调。','speaker':'李工'}]
        s.save_revision(mid,segments,'manual',expected=0)
        doc=MeetingMinutes(title='项目评审纪要',style='ai')
        aid=uid();s.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(aid,mid,1,'meeting_minutes',encode(doc.model_dump()),encode({'edited':True})))
        yield c,s,mid,aid,segments

def note(setup,**kwargs):
    c,_,mid,_,_=setup
    r=c.post(f'/api/meetings/{mid}/notes',json={'raw_text':'李工 联调 周五','captured_at':15,**kwargs});assert r.status_code==201
    return r.json()

def use_body(setup,nid,**kwargs):
    c,_,mid,aid,_=setup;ctx=c.get(f'/api/meetings/{mid}/notes/{nid}/review').json()
    return {'kind':'minutes','fingerprint':ctx['fingerprint'],'revision':ctx['revision'],'content':'李工在周五完成接口联调。','source_segment_ids':['a'],'section':'action_items','minutes_id':aid,'confirmed':True,**kwargs}

def test_marker_validation_and_fill_preserves_time(setup):
    c,s,mid,_,_=setup
    assert c.post(f'/api/meetings/{mid}/notes',json={'raw_text':''}).status_code==422
    n=note(setup,raw_text='',is_marker=True)
    assert n['note_scope']=='personal' and n['is_marker']==1
    assert c.post(f"/api/meetings/{mid}/notes/{n['id']}/expand",json={}).status_code==409
    assert c.put(f"/api/meetings/{mid}/notes/{n['id']}",json={'raw_text':'待核实日期','note_type':'question','is_marker':False}).status_code==200
    after=s.one('SELECT * FROM meeting_notes WHERE id=?',(n['id'],));assert after['captured_at']==15 and not after['is_marker']

def test_personal_is_blocked_from_minutes_even_with_valid_evidence(setup):
    c,s,mid,_,_=setup;n=note(setup)
    response=c.post(f"/api/meetings/{mid}/notes/{n['id']}/use",json=use_body(setup,n['id']))
    assert response.status_code==409 and '个人备注' in response.json()['detail']
    assert len(s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'"))==1

def test_adopt_is_explicit_versioned_and_idempotent(setup):
    c,s,mid,aid,_=setup;n=note(setup,note_scope='meeting')
    route=f"/api/meetings/{mid}/notes/{n['id']}/use";body=use_body(setup,n['id'])
    assert c.post(route,json={**body,'confirmed':False}).status_code==409
    assert c.post(route,json={**body,'source_segment_ids':[]}).status_code==409
    assert c.post(route,json={**body,'source_segment_ids':['missing']}).status_code==409
    response=c.post(route,json=body);assert response.status_code==200
    fresh=response.json()['id'];assert fresh!=aid
    result=s.one('SELECT * FROM artifacts WHERE id=?',(fresh,));payload=json.loads(result['payload'])
    assert payload['action_items'][0]['content']==body['content']
    assert json.loads(result['provenance'])['adopted_notes'][0]['note_id']==n['id']
    assert c.post(route,json=body).json()['reused']
    assert len(s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'"))==2
    assert s.one('SELECT raw_text FROM meeting_notes WHERE id=?',(n['id'],))['raw_text']==n['raw_text']
    assert json.loads(s.one('SELECT payload FROM artifacts WHERE id=?',(aid,))['payload'])['action_items']==[]

def test_stale_note_transcript_and_minutes_block_use(setup):
    c,s,mid,aid,segments=setup;n=note(setup,note_scope='meeting');body=use_body(setup,n['id']);route=f"/api/meetings/{mid}/notes/{n['id']}/use"
    s.execute('UPDATE meeting_notes SET raw_text=? WHERE id=?',('修改后的记录',n['id']))
    assert c.post(route,json=body).status_code==409
    body=use_body(setup,n['id']);newid=uid()
    s.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) SELECT ?,meeting_id,revision,kind,payload,provenance FROM artifacts WHERE id=?',(newid,aid))
    assert c.post(route,json=body).status_code==409
    body=use_body(setup,n['id'],minutes_id=newid);s.save_revision(mid,segments,'manual',expected=1)
    assert c.post(route,json=body).status_code==409

def test_todos_are_manual_separate_persistent_and_refer_back(setup):
    c,s,mid,_,_=setup;n=note(setup,note_type='action')
    assert c.get(f'/api/meetings/{mid}').json()['note_todos']==[]
    body=use_body(setup,n['id'],kind='todo',content='明天核实联调日期',source_segment_ids=[])
    route=f"/api/meetings/{mid}/notes/{n['id']}/use";response=c.post(route,json=body);assert response.status_code==200;tid=response.json()['id']
    assert c.post(route,json=body).json()['id']==tid
    row=c.get(f'/api/meetings/{mid}').json()['note_todos'][0]
    assert row['owner']=='' and row['due_date']=='' and not row['stale'] and row['note_scope']=='personal'
    assert c.put(f'/api/meetings/{mid}/note-todos/{tid}',json={'status':'done','owner':'我','due_date':'明天'}).status_code==200
    c.delete(f"/api/meetings/{mid}/notes/{n['id']}")
    row=c.get(f'/api/meetings/{mid}').json()['note_todos'][0]
    assert row['status']=='done' and row['owner']=='我' and row['stale']
    assert len(s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'"))==1

def test_recording_marker_and_scope_survive_finish(setup):
    from app.recordings import RecordingStore
    c,s,_,_,_=setup;r=c.post('/api/recordings',json={}).json();rid=r['id']
    assert c.put(f'/api/recordings/{rid}/metadata',json={'notes':[{'captured_at':.5,'raw_text':'','is_marker':True},{'captured_at':.5,'raw_text':'明确联调','note_scope':'meeting'}],'duration':1,'chunk_count':1}).status_code==200
    out=io.BytesIO()
    with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*16000)
    c.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part.webm',out.getvalue())})
    result=c.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1});assert result.status_code==200
    notes=result.json()['notes'];assert len(notes)==2 and notes[0]['is_marker'] and notes[0]['raw_text']=='' and notes[1]['note_scope']=='meeting'

def test_personal_scope_change_invalidates_expansion(setup):
    c,s,mid,_,_=setup;n=note(setup)
    payload={'note_id':n['id'],'raw_text':n['raw_text'],'note_type':'note','captured_at':15,'note_scope':'personal','status':'supported','text':'李工联调','reason':'','evidence':[{'segment_id':'a','quote':'李工'}]}
    aid=uid();s.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(aid,mid,1,'note_expansion',encode(payload),'{}'))
    assert c.put(f"/api/meetings/{mid}/notes/{n['id']}",json={'raw_text':n['raw_text'],'note_type':'note','note_scope':'meeting'}).status_code==200
    assert c.post(f"/api/meetings/{mid}/notes/{n['id']}/accept-expansion",json={'artifact_id':aid}).status_code==409
    assert c.get(f'/api/meetings/{mid}').json()['notes'][0]['expansion']['stale']
