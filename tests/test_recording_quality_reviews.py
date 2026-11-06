"""Manual conclusions annotate evidence; they never repair audio or override observations."""
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app.main import create_app
from app.lifecycle import Lifecycle
from tests.test_recording_completion import publish


def anomaly(client,rid,seconds=10):
    journal=client.app.state.lifecycle
    journal.transition(rid,'microphone_input','no_data',{'duration_seconds':seconds})
    journal.transition(rid,'microphone_input','receiving',{'duration_seconds':seconds+5})
    return journal.anomalies(rid)['items'][0]


def test_review_is_durable_reversible_and_does_not_modify_capture_evidence(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);item=anomaly(client,rid);journal=client.app.state.lifecycle
        url=f'/api/meetings/{rid}/recording-quality/{item["id"]}/review'
        audio=client.app.state.store.directory(rid)/'original';before=audio.read_bytes()
        for status,version in [('clear',0),('uncertain',1),('gap',2),('pending',3)]:
            assert client.post(url,json={'status':status,'expected_version':version}).status_code==200
            current=Lifecycle(tmp_path).anomalies(rid)['items'][0]
            assert current['review_status']==status and current['review_version']==version+1
            for key in ('component','state','start_seconds','end_seconds','closed_at'):
                assert current[key]==item[key]
        assert before==audio.read_bytes()
        assert not client.app.state.store.all('SELECT id FROM jobs WHERE meeting_id=?',(rid,))


def test_lost_response_retry_is_idempotent_but_conflicting_conclusion_is_rejected(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);item=anomaly(client,rid)
        url=f'/api/meetings/{rid}/recording-quality/{item["id"]}/review'
        body={'status':'clear','expected_version':0}
        assert client.post(url,json=body).status_code==200
        assert client.post(url,json=body).status_code==200
        assert client.post(url,json=dict(body,status='gap')).status_code==409
        current=client.get(f'/api/meetings/{rid}/recording-quality').json()['items'][0]
        assert current['review_version']==1 and current['review_status']=='clear'


def test_concurrent_different_conclusions_do_not_silently_overwrite_each_other(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);item=anomaly(client,rid)
        url=f'/api/meetings/{rid}/recording-quality/{item["id"]}/review'
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda s:client.post(url,json={'status':s,'expected_version':0}),['clear','gap']))
        assert sorted(r.status_code for r in responses)==[200,409]


def test_pending_pagination_does_not_hide_older_items_or_uncertain_conclusions(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client)
        for i in range(25):anomaly(client,rid,i*2)
        base=f'/api/meetings/{rid}/recording-quality'
        rows=client.get(base+'?view=all').json()
        assert rows['pending_count']==25 and len(rows['items'])==20
        aid=rows['items'][0]['id']
        assert client.post(f'{base}/{aid}/review',json={'status':'uncertain','expected_version':0}).status_code==200
        aid2=rows['items'][1]['id']
        assert client.post(f'{base}/{aid2}/review',json={'status':'clear','expected_version':0}).status_code==200
        first=client.get(base+'?view=pending').json();last=client.get(base+'?view=pending&offset=20').json()
        assert first['pending_count']==24 and first['reviewed_count']==1 and len(last['items'])==4
        assert aid in [x['id'] for x in first['items']] and aid2 not in [x['id'] for x in first['items']]
        assert not set(x['id'] for x in first['items']) & set(x['id'] for x in last['items'])
        assert client.get(base+'?offset=-1').status_code==422


def test_open_foreign_and_unknown_observations_cannot_be_reviewed(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);other=publish(client);journal=client.app.state.lifecycle
        journal.transition(rid,'capture','unknown',{'duration_seconds':10});aid=journal.anomalies(rid)['items'][0]['id']
        body={'status':'clear','expected_version':0}
        assert client.post(f'/api/meetings/{rid}/recording-quality/{aid}/review',json=body).status_code==409
        assert client.post(f'/api/meetings/{other}/recording-quality/{aid}/review',json=body).status_code==404
        assert client.post(f'/api/meetings/{rid}/recording-quality/99999/review',json=body).status_code==404
        assert client.post(f'/api/meetings/{rid}/recording-quality/{aid}/review',json=dict(body,status='invented')).status_code==422
        assert client.post(f'/api/meetings/{rid}/recording-quality/{aid}/review',json=dict(body,expected_version=-1)).status_code==422


def test_reviewed_gap_is_not_pruned_by_new_automatic_observations(tmp_path):
    journal=Lifecycle(tmp_path);rid='a'*32
    journal.transition(rid,'storage','unconfirmed',{'duration_seconds':1})
    journal.transition(rid,'storage','writing',{'duration_seconds':2})
    item=journal.anomalies(rid)['items'][0];journal.review_anomaly(rid,item['id'],'gap',0)
    for i in range(205):
        journal.transition(rid,'storage','unconfirmed',{'duration_seconds':3+i*2})
        journal.transition(rid,'storage','writing',{'duration_seconds':4+i*2})
    result=Lifecycle(tmp_path).anomalies(rid,300)
    assert result['total_count']==206 and result['retained_count']==201 and result['gap_count']==1
    assert any(row['id']==item['id'] and row['review_status']=='gap' for row in result['items'])


def test_failed_review_write_does_not_claim_success_and_can_be_retried(tmp_path,monkeypatch):
    import sqlite3
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);item=anomaly(client,rid);journal=client.app.state.lifecycle
        url=f'/api/meetings/{rid}/recording-quality/{item["id"]}/review';body={'status':'gap','expected_version':0}
        def fail():raise sqlite3.OperationalError('private-disk-location')
        with monkeypatch.context() as scoped:
            scoped.setattr(journal,'connect',fail)
            response=client.post(url,json=body)
            assert response.status_code==503 and 'private-' not in response.text
        assert journal.anomalies(rid)['items'][0]['review_status']=='pending'
        assert client.post(url,json=body).status_code==200
