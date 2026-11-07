"""Exercise translations against actual confirmed sources and an isolated model."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.live_results import LiveResults
from app.recordings import RecordingStore


@pytest.fixture
def live(tmp_path, monkeypatch):
    app = create_app(tmp_path, start_worker=False)
    calls = []

    def model(url, headers, payload, timeout):
        calls.append(json.loads(payload['messages'][1]['content'])['source'])
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"translation":"我们星期五交付。"}'}}]}

    monkeypatch.setattr('app.chinese_record.request_once', model)
    with TestClient(app) as client:
        store = app.state.store
        store.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES('local','Local','local','http://127.0.0.1:1234/v1','fixture')")
        rid = client.post('/api/recordings', json={}).json()['id']
        recordings = RecordingStore(store)
        live = LiveResults(store, recordings)
        aid, _ = live.begin(rid, 'browser', 'whisper', 0, 6, b'source', [{'source_language': 'auto'}])
        texts = ['我们讨论时间。', 'We will deliver on Friday.', 'こんにちは']
        live.complete(aid, [{'start': i * 2, 'end': i * 2 + 2, 'text': text} for i, text in enumerate(texts)])
        yield client, store, rid, aid, texts, calls


def test_confirmed_mixed_segments_keep_source_and_timestamps(live):
    client, store, rid, aid, texts, calls = live
    results = [client.post(f'/api/recordings/{rid}/chinese-record/{aid}/{i}',
                           json={'profile_id': 'local', 'expected_text': text}).json() for i, text in enumerate(texts)]
    assert [row['status'] for row in results] == ['original', 'translated', 'unknown']
    assert results[1] == {'status': 'translated', 'text': '我们星期五交付。', 'start': 2, 'end': 4}
    assert calls == [texts[1]]
    assert json.loads(store.one('SELECT segments FROM live_asr_ranges WHERE id=?', (aid,))['segments'])[1]['text'] == texts[1]


def test_changed_or_unconfirmed_source_is_not_sent(live):
    client, store, rid, aid, _, calls = live
    url = f'/api/recordings/{rid}/chinese-record/{aid}/1'
    assert client.post(url, json={'profile_id': 'local', 'expected_text': 'wrong'}).status_code == 409
    store.execute("UPDATE live_asr_ranges SET state='preview' WHERE id=?", (aid,))
    assert client.post(url, json={'profile_id': 'local', 'expected_text': 'We will deliver on Friday.'}).status_code == 409
    assert not calls
