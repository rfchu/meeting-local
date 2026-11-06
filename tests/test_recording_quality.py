"""Durable abnormal observations are independent from publication and ASR coverage."""
import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.lifecycle import Lifecycle
from app.recordings import RecordingStore
from app.live_results import LiveResults
from tests.test_recording_completion import publish


def test_saved_audio_and_full_asr_coverage_do_not_erase_capture_observations(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);journal=client.app.state.lifecycle;store=client.app.state.store
        journal.transition(rid,'microphone_input','no_data',{'duration_seconds':10,'raw_text':'secret'})
        journal.transition(rid,'microphone_input','no_data',{'duration_seconds':15})
        journal.transition(rid,'microphone_input','receiving',{'duration_seconds':20})
        cache=LiveResults(store,RecordingStore(store))
        aid,_=cache.begin(rid,'browser','qwen',0,30,b'audio',{});cache.complete(aid,[{'text':'private-transcript'}])
        report=client.get(f'/api/recordings/{rid}/completion?engine=qwen').json()
        assert report['audio_saved'] and report['coverage']['uncovered_seconds']==0
        quality=report['quality'];assert quality['total_count']==1 and quality['monitored']
        item=quality['items'][0]
        assert (item['start_seconds'],item['end_seconds'],item['end_state'])==(10,20,'receiving')
        assert item['closed_at'] is not None
        assert client.get(f'/api/meetings/{rid}/recording-quality').json()==quality
        assert 'private-' not in json.dumps(quality) and 'secret' not in json.dumps(quality)


def test_observations_survive_event_pruning_and_journal_recreation_with_bounded_details(tmp_path):
    journal=Lifecycle(tmp_path);rid='a'*32
    for i in range(205):
        journal.transition(rid,'storage','unconfirmed',{'duration_seconds':i*2})
        journal.transition(rid,'storage','writing',{'duration_seconds':i*2+1})
    with journal.connect() as db:db.execute('DELETE FROM events')
    restored=Lifecycle(tmp_path);report=restored.anomalies(rid,300)
    assert report['available'] and report['total_count']==205 and len(report['items'])==200
    assert report['items'][0]['start_seconds']==408
    assert restored.events(rid)==[]


def test_unknown_timeline_is_not_invented_and_stopping_is_not_called_recovery(tmp_path):
    journal=Lifecycle(tmp_path);rid='a'*32
    journal.record('capture.interrupted',rid)
    item=journal.anomalies(rid)['items'][0]
    assert item['start_seconds'] is None and item['end_seconds'] is None
    journal.transition(rid,'capture','interrupted',{'duration_seconds':15})
    assert journal.anomalies(rid)['items'][0]['start_seconds'] is None
    journal.transition(rid,'capture','stopped',{'duration_seconds':20})
    item=journal.anomalies(rid)['items'][0]
    assert item['start_seconds'] is None and item['end_seconds']==20 and item['end_state']=='stopped'


def test_quality_reads_do_not_mutate_audio_journal_or_job_queue(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);journal=client.app.state.lifecycle;store=client.app.state.store
        journal.transition(rid,'storage','unconfirmed',{'duration_seconds':15})
        original=store.directory(rid)/'original';before=original.read_bytes()
        events=journal.events(rid);items=journal.anomalies(rid)
        for path in (f'/api/meetings/{rid}/recording-quality',f'/api/recordings/{rid}/completion',f'/api/recordings/{rid}/diagnostics'):
            assert client.get(path).status_code==200
        assert before==original.read_bytes() and journal.events(rid)==events and journal.anomalies(rid)==items
        assert store.all('SELECT id FROM jobs WHERE meeting_id=?',(rid,))==[]


def test_unavailable_or_missing_history_is_not_a_clean_bill_of_health(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);journal=client.app.state.lifecycle
        # Legacy checkpoint rows predate durable quality observations.
        with journal.connect() as db:
            db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)',(rid,'capture','recording',1,'{}'))
        assert not client.get(f'/api/meetings/{rid}/recording-quality').json()['monitored']
        journal.available=False
        report=client.get(f'/api/recordings/{rid}/completion').json()
        assert report['audio_saved'] and not report['quality']['available']
        assert client.get('/api/meetings/'+'0'*32+'/recording-quality').status_code==404
