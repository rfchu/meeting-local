"""One read-only recording summary follows the meeting, not its current UI tab."""
from fastapi.testclient import TestClient
from app.main import create_app
from tests.test_recording_completion import publish
from tests.test_recording_quality_reviews import anomaly


def test_meeting_summary_tracks_reviews_without_changing_content_or_checkpoints(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);item=anomaly(client,rid)
        meeting=client.get(f'/api/meetings/{rid}').json()
        summary=meeting['recording_quality']
        assert 'items' not in summary and summary['pending_count']==1
        full=client.get(f'/api/meetings/{rid}/recording-quality').json();full.pop('items')
        assert summary==full
        endpoint=f'/api/meetings/{rid}/recording-quality/{item["id"]}/review'
        assert client.post(endpoint,json={'status':'gap','expected_version':0}).status_code==200
        fresh=client.get(f'/api/meetings/{rid}').json()
        assert fresh['recording_quality']['pending_count']==0 and fresh['recording_quality']['gap_count']==1
        for key in ('segments','revision','review','artifacts','notes'):
            assert fresh[key]==meeting[key]
        assert client.post(endpoint,json={'status':'clear','expected_version':1}).status_code==200
        cleared=client.get(f'/api/meetings/{rid}').json()['recording_quality']
        assert cleared['pending_count']==cleared['gap_count']==0 and cleared['reviewed_count']==1


def test_unavailable_journal_does_not_break_meeting_or_misreport_healthy_summary(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);client.app.state.lifecycle.available=False
        response=client.get(f'/api/meetings/{rid}')
        assert response.status_code==200
        quality=response.json()['recording_quality']
        assert quality['recorded'] and not quality['available']


def test_confirmed_gap_filter_finds_older_reviewed_items_outside_latest_page(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=publish(client);first=anomaly(client,rid)
        for i in range(25):anomaly(client,rid,20+i*2)
        base=f'/api/meetings/{rid}/recording-quality'
        assert first['id'] not in [row['id'] for row in client.get(base).json()['items']]
        assert client.post(f'{base}/{first["id"]}/review',json={'status':'gap','expected_version':0}).status_code==200
        gaps=client.get(base+'?view=gaps').json()
        assert gaps['gap_count']==1 and [row['id'] for row in gaps['items']]==[first['id']]
        assert client.get(base+'?view=gaps&offset=20').json()['items']==[]
