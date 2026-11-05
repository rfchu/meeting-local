import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from app.main import create_app
from app.recordings import RecordingStore
from app.live_results import LiveResults


def publish(client):
    rid=client.post('/api/recordings',json={}).json()['id']
    assert client.put(f'/api/recordings/{rid}/metadata',json={'duration':30,'chunk_count':1,'notes':[{'captured_at':1,'raw_text':'private-note','note_type':'note'}]}).status_code==200
    client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'saved-audio')})
    assert client.post(f'/api/recordings/{rid}/finish',json={'chunk_count':1}).status_code==200
    return rid


def test_completion_receipt_uses_quick_plan_and_never_exports_content(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);store=client.app.state.store;recordings=RecordingStore(store);live=LiveResults(store,recordings)
        for start,end,state in [(0,15,'completed'),(5,20,'completed'),(20,25,'preview'),(25,30,'failed')]:
            aid,_=live.begin(rid,'browser','qwen',start,end-start,str(start).encode(),{})
            if state=='completed':live.complete(aid,[{'text':'private-transcript'}])
            elif state=='preview':live.preview(aid)
            else:live.fail(aid,'private-token')
        before=(recordings.folder(rid)/'session.json').read_bytes()
        report=client.get(f'/api/recordings/{rid}/completion?engine=qwen').json()
        assert report['audio_saved'] and report['notes_count']==1 and report['saved_bytes']==11
        # The overlapping 5..20 result cannot be spliced without reusing text.
        assert report['coverage']['covered_seconds']==15
        assert report['coverage']['gaps']==[{'start':15,'end':30}]
        assert report['coverage']['failed_attempts']==1
        assert 'private-' not in json.dumps(report)
        assert before==(recordings.folder(rid)/'session.json').read_bytes()
        (store.directory(rid)/'original').unlink()
        assert not client.get(f'/api/recordings/{rid}/completion').json()['audio_saved']


def test_handoff_concurrent_retries_and_lost_response_never_duplicate_jobs(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);url=f'/api/meetings/{rid}/jobs'
        body={'completion_mode':'quick','engine':'qwen','recording_handoff':True}
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:client.post(url,json=body),range(2)))
        assert [r.status_code for r in responses]==[201,201]
        ids={r.json()['id'] for r in responses};assert len(ids)==1
        store=client.app.state.store;jid=ids.pop()
        for state in ('completed','failed','cancelled'):
            store.execute('UPDATE jobs SET state=? WHERE id=?',(state,jid))
            result=client.post(url,json=dict(body,engine='whisper')).json()
            assert result=={'id':jid,'reused':True}
        assert len(store.all('SELECT id FROM jobs WHERE meeting_id=?',(rid,)))==1
        assert (store.directory(rid)/'original').read_bytes()==b'saved-audio'
        # An explicit new transcription remains a separate action.
        result=client.post(url,json=dict(body,recording_handoff=False))
        assert result.status_code==201 and result.json()['id']!=jid


def test_handoff_preflight_failure_keeps_audio_and_can_retry(tmp_path,monkeypatch):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);url=f'/api/meetings/{rid}/jobs';store=client.app.state.store
        body={'completion_mode':'quick','recording_handoff':True,'minutes_followup':{}}
        def fail(*_):raise ValueError('模型配置未就绪')
        monkeypatch.setattr('app.main.prepare_auto_minutes',fail)
        assert client.post(url,json=body).status_code==409
        assert not store.all('SELECT id FROM jobs WHERE meeting_id=?',(rid,))
        assert client.get(f'/api/recordings/{rid}/completion').json()['audio_saved']
        monkeypatch.setattr('app.main.prepare_auto_minutes',lambda *_:{'model':'fixture-only'})
        assert client.post(url,json=body).status_code==201
        assert len(store.all('SELECT id FROM jobs WHERE meeting_id=?',(rid,)))==1


def test_unpublished_report_does_not_claim_saved_audio(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id']
        report=client.get(f'/api/recordings/{rid}/completion').json()
        assert not report['published'] and not report['audio_saved']
        assert client.get(f'/api/recordings/{rid}/completion?engine=invalid').status_code==422
        assert client.get('/api/recordings/'+'0'*32+'/completion').status_code==404
