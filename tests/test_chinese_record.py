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


def test_retry_reuses_result_and_changed_source_or_model_does_not(live):
    client, store, rid, aid, texts, calls = live
    url = f'/api/recordings/{rid}/chinese-record/{aid}/1'
    body = {'profile_id': 'local', 'expected_text': texts[1]}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 200
    assert len(calls) == 1
    store.execute("UPDATE llm_configs SET model='second' WHERE id='local'")
    assert client.post(url, json=body).status_code == 200
    assert len(calls) == 2
    segments = json.loads(store.one('SELECT segments FROM live_asr_ranges WHERE id=?', (aid,))['segments'])
    segments[1]['text'] = 'We will deliver on Monday.'
    store.execute('UPDATE live_asr_ranges SET segments=? WHERE id=?', (json.dumps(segments), aid))
    assert client.post(url, json=body).status_code == 409
    assert client.post(url, json=dict(body, expected_text=segments[1]['text'])).status_code == 200
    assert calls[-1] == segments[1]['text'] and len(calls) == 3


def test_failure_and_late_result_are_not_published(live, monkeypatch):
    client, store, rid, aid, texts, _ = live
    url = f'/api/recordings/{rid}/chinese-record/{aid}/1'
    body = {'profile_id': 'local', 'expected_text': texts[1]}

    def interrupted(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr('app.chinese_record.request_once', interrupted)
    assert client.post(url, json=body).status_code == 503

    def stale(*args, **kwargs):
        store.execute("UPDATE live_asr_ranges SET state='preview' WHERE id=?", (aid,))
        return {'choices': [{'message': {'content': '{"translation":"过期译文"}'}}]}

    monkeypatch.setattr('app.chinese_record.request_once', stale)
    assert client.post(url, json=body).status_code == 409
    assert not list(store.directory(rid).glob('chinese-record/*.json'))


def test_formal_record_reuses_live_translation_and_reports_missing(live):
    client, store, rid, aid, texts, calls = live
    assert client.post(f'/api/recordings/{rid}/chinese-record/{aid}/1',
                       json={'profile_id': 'local', 'expected_text': texts[1]}).status_code == 200
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)', (rid, '中文记录', 'test.wav'))
    segments = [
        {'id': 'zh', 'start': 0., 'end': 2., 'text': texts[0]},
        {'id': 'en', 'start': 2., 'end': 4., 'text': texts[1]},
        {'id': 'unknown', 'start': 4., 'end': 6., 'text': texts[2]},
        {'id': 'pending', 'start': 6., 'end': 8., 'text': 'We will review this.'},
    ]
    revision = store.save_revision(rid, segments, 'manual')
    root = f'/api/meetings/{rid}/chinese-record'
    data = client.get(root, params={'profile_id': 'local', 'limit': 2}).json()
    assert data['counts'] == {'original': 1, 'translated': 1, 'pending': 1, 'unknown': 1}
    assert data['items'][1]['original'] == texts[1]
    assert data['items'][1]['text'] == '我们星期五交付。'
    assert data['items'][1]['start'] == 2
    assert len(calls) == 1
    page = client.get(root, params={'profile_id': 'local', 'offset': 2}).json()
    assert [row['status'] for row in page['items']] == ['unknown', 'pending']
    body = {'profile_id': 'local', 'expected_text': segments[3]['text'], 'revision': revision}
    assert client.post(root + '/pending', json=body).status_code == 200
    assert client.get(root, params={'profile_id': 'local'}).json()['counts']['pending'] == 0
    segments[3]['text'] = 'We will review this next week.'
    store.save_revision(rid, segments, 'manual')
    assert client.post(root + '/pending', json=body).status_code == 409
    assert client.get(root, params={'profile_id': 'local'}).json()['counts']['pending'] == 1


def test_legacy_meeting_is_readable_without_translation_model(live):
    client, store, rid, _, _, calls = live
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)', (rid, '旧会议', 'test.wav'))
    path = f'/api/meetings/{rid}/chinese-record'
    assert client.get(path).json()['items'] == []
    store.save_revision(rid, [{'id': 'old', 'text': '原有中文。', 'start': 0, 'end': 2}], 'manual')
    result = client.get(path).json()
    assert result['items'][0]['status'] == 'original'
    assert not calls


def test_qwen_translation_disables_reasoning_and_ignores_thinking(live, monkeypatch):
    from urllib.error import HTTPError

    client, store, rid, aid, texts, _ = live
    store.execute("UPDATE llm_configs SET model='qwen3.6' WHERE id='local'")
    url = f'/api/recordings/{rid}/chinese-record/{aid}/1'
    body = {'profile_id': 'local', 'expected_text': texts[1]}
    calls = []
    def request(endpoint, headers, payload, timeout):
        calls.append(endpoint)
        if endpoint.endswith('/api/v1/chat'):
            assert payload['reasoning'] == 'off' and payload['store'] is False
            return {'output': [{'type':'reasoning','content':'{"translation":"不是最终译文"}'},
                               {'type':'message','content':'{"translation":"我们将于周五交付。"}'}]}
        raise AssertionError(endpoint)

    monkeypatch.setattr('app.chinese_record.request_once', request)
    response = client.post(url,json=body)
    assert response.status_code == 200, response.text
    assert response.json()['text'] == '我们将于周五交付。' and len(calls) == 1

    store.execute("UPDATE llm_configs SET model='qwen3.6-compatible' WHERE id='local'")
    def compatible(endpoint, headers, payload, timeout):
        if endpoint.endswith('/api/v1/chat'):
            raise HTTPError(endpoint,404,'not supported',None,None)
        assert payload['messages'][0]['content'].startswith('/no_think')
        return {'choices':[{'finish_reason':'length','message':{
            'content':'','reasoning_content':'{"translation":"不能保存的推理"}'}}]}

    monkeypatch.setattr('app.chinese_record.request_once', compatible)
    response = client.post(url,json=body)
    assert response.status_code == 409 and '输出上限' in response.json()['detail']
    assert len(list(store.directory(rid).glob('chinese-record/*.json'))) == 1
