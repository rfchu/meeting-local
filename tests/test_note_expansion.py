import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.note_expansion import Expansion,Evidence,nearby_segments,validate_expansion,publish
from app.db import uid,encode
from app.worker import Cancelled

@pytest.fixture
def setup(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid();store.directory(mid).mkdir(exist_ok=True)
        store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,'测试','test.wav',300))
        segments=[{'id':'a','start':100,'end':105,'text':'李工在周五完成接口联调。','speaker':'发言人 1'}]
        store.save_revision(mid,segments,'manual',expected=0)
        note=client.post(f'/api/meetings/{mid}/notes',json={'captured_at':103,'note_type':'action','raw_text':'李工 联调 周五'}).json()
        monkeypatch.setattr('app.main.LMStudioProvider.resolve_model',lambda *_:'test-model')
        result=Expansion(status='supported',text='李工在周五完成接口联调。',reason='',evidence=[Evidence(segment_id='a',quote='李工在周五完成接口联调。')])
        monkeypatch.setattr('app.note_expansion.generate_expansion',lambda *args:result)
        yield client,store,mid,note,segments,result

def run(setup):
    c,store,mid,note,_,_=setup
    response=c.post(f"/api/meetings/{mid}/notes/{note['id']}/expand",json={});assert response.status_code==201
    jid=response.json()['id'];store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    c.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    return c.get(f'/api/meetings/{mid}').json()['notes'][0]

def test_suggestion_is_separate_accept_preserves_raw_and_revision(setup):
    c,store,mid,note,segments,_=setup;fresh=run(setup)
    assert fresh['accepted_text']=='' and fresh['raw_text']==note['raw_text']
    aid=fresh['expansion']['id'];route=f"/api/meetings/{mid}/notes/{note['id']}/accept-expansion"
    assert c.post(route,json={'artifact_id':aid}).status_code==200
    after=c.get(f'/api/meetings/{mid}').json();accepted=after['notes'][0]
    assert after['revision']==1 and after['segments']==segments
    assert accepted['raw_text']==note['raw_text'] and accepted['status']=='accepted'
    assert accepted['source_segment_ids']==['a'] and accepted['expansion_revision']==1
    assert c.post(route,json={'artifact_id':aid}).status_code==200
    store.save_revision(mid,segments,'manual',expected=1)
    after=c.get(f'/api/meetings/{mid}').json()['notes'][0]
    assert after['expansion_stale'] and after['expansion']['stale']
    assert c.post(route,json={'artifact_id':aid}).status_code==409

def test_modified_note_blocks_accept_and_clears_accepted(setup):
    c,_,mid,note,_,_=setup;fresh=run(setup)
    route=f"/api/meetings/{mid}/notes/{note['id']}"
    c.put(route,json={'raw_text':'改为待确认','note_type':'question'})
    fresh2=c.get(f'/api/meetings/{mid}').json()['notes'][0]
    assert fresh2['expansion']['stale']
    assert c.post(route+'/accept-expansion',json={'artifact_id':fresh['expansion']['id']}).status_code==409

def test_no_nearby_evidence_no_model_call(setup,monkeypatch):
    c,store,mid,note,_,_=setup
    store.execute('UPDATE meeting_notes SET captured_at=299 WHERE id=?',(note['id'],))
    monkeypatch.setattr('app.note_expansion.generate_expansion',lambda *_:pytest.fail('must not call model'))
    fresh=run(setup)
    assert fresh['expansion']['payload']['status']=='insufficient'
    assert c.post(f"/api/meetings/{mid}/notes/{note['id']}/accept-expansion",json={'artifact_id':fresh['expansion']['id']}).status_code==409

def test_exact_quote_and_scope_validation(setup):
    _,_,_,_,segments,result=setup
    assert validate_expansion(result,segments)==result
    for sid,quote in [('missing','李工'),('a','周五完成上线')]:
        bad=result.model_copy(update={'evidence':[Evidence(segment_id=sid,quote=quote)]})
        with pytest.raises(ValueError):validate_expansion(bad,segments)
    with pytest.raises(ValueError):validate_expansion(Expansion(status='insufficient',text='猜测',reason='',evidence=[]),segments)
    assert nearby_segments(segments,103)==segments
    assert nearby_segments(segments,299)==[]
    assert nearby_segments(segments,103,max_chars=1)==[]

def test_cancellation_no_publication_and_queue_conflict(setup):
    c,store,mid,note,segments,result=setup;route=f"/api/meetings/{mid}/notes/{note['id']}/expand"
    jid=c.post(route,json={}).json()['id']
    assert c.post(route,json={}).status_code==409
    c.post(f'/api/jobs/{jid}/cancel')
    job=store.one('SELECT * FROM jobs WHERE id=?',(jid,))
    with pytest.raises(RuntimeError):publish(store,job,result,segments)
    with pytest.raises(Cancelled):c.app.state.worker.process(job)
    assert not store.all("SELECT * FROM artifacts WHERE kind='note_expansion'")

def test_provider_request_schema_and_original_quote(setup,tmp_path):
    from app.note_expansion import generate_expansion
    from app.analysis import LMStudioProvider
    _,_,_,note,segments,result=setup
    def fake_run(args,env):
        request=json.loads(open(args[args.index('--request')+1]).read())
        assert request['response_format']['json_schema']['name']=='note_expansion'
        assert request['response_format']['json_schema']['schema']['properties']['evidence']['items']['properties']['segment_id']['enum']==['a']
        open(args[args.index('--output')+1],'w').write(encode(result.model_dump()))
    assert generate_expansion(LMStudioProvider(),'test',note,segments,tmp_path/'result',fake_run)==result

def test_deleted_note_and_wrong_target_cannot_accept(setup):
    c,store,mid,note,_,_=setup;fresh=run(setup)
    other=c.post(f'/api/meetings/{mid}/notes',json={'raw_text':'其他简记'}).json()
    assert c.post(f"/api/meetings/{mid}/notes/{other['id']}/accept-expansion",json={'artifact_id':fresh['expansion']['id']}).status_code==400
    c.delete(f"/api/meetings/{mid}/notes/{note['id']}")
    assert c.post(f"/api/meetings/{mid}/notes/{note['id']}/accept-expansion",json={'artifact_id':fresh['expansion']['id']}).status_code==404
