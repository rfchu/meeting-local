"""Tests for shorthand note HTTP routes and background synthesis."""
import time
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.recordings import RecordingStore


@pytest.fixture
def test_client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        yield client


def create_mock_recording(client):
    store = client.app.state.store
    recordings = RecordingStore(store)
    session = recordings.create('audio/webm')
    return session['id']


def test_post_raw_shorthand_note(test_client):
    rid = create_mock_recording(test_client)

    start = time.monotonic()
    response = test_client.post(
        f"/api/recordings/{rid}/notes/shorthand",
        json={
            "raw_text": "前端开发下周一联调",
            "captured_at": 12.5,
            "synthesize": False,
            "speaker_hint": "李四",
        },
    )
    elapsed = time.monotonic() - start

    assert response.status_code == 201
    data = response.json()
    assert data["id"]
    assert data["raw_text"] == "前端开发下周一联调"
    assert data["status"] == "raw"
    assert data["captured_at"] == 12.5
    assert data["speaker_hint"] == "李四"
    assert elapsed < 0.1  # Fast return well under 100ms

    # Check that note is persistent in recording session
    recordings = RecordingStore(test_client.app.state.store)
    saved = recordings.get_note(rid, data["id"])
    assert saved is not None
    assert saved["raw_text"] == "前端开发下周一联调"


def test_post_shorthand_note_validation(test_client):
    rid = create_mock_recording(test_client)

    # Empty raw_text should fail
    resp = test_client.post(
        f"/api/recordings/{rid}/notes/shorthand",
        json={"raw_text": "   "},
    )
    assert resp.status_code in (400, 422)

    # Non-existent recording should 404
    resp = test_client.post(
        f"/api/recordings/{'a'*32}/notes/shorthand",
        json={"raw_text": "测试文本"},
    )
    assert resp.status_code == 404


def test_post_shorthand_with_synthesize(test_client, monkeypatch):
    rid = create_mock_recording(test_client)

    assert test_client.post("/api/llm-configs", json={
        "name": "测试", "provider_type": "local", "base_url": "http://127.0.0.1:1234/v1", "model": "test"
    }).status_code == 201
    monkeypatch.setattr(test_client.app.state.services.live_sessions, "snapshot", lambda _: {
        "segments": [{"start": 10, "end": 15, "text": "下周一开始前后端联调。", "speaker": "李四"}]
    })

    async def mock_call(messages, schema):
        return {
            "summary": "前端联调定于下周一启动",
            "details": "李四确认与后端按期对接",
            "speakers": ["李四"],
            "anchor_start": 10.0,
            "anchor_end": 15.0,
            "evidence_quote": "下周一开始前后端联调。",
        }

    # Patch synthesizer call
    from app import shorthand
    monkeypatch.setattr(shorthand.ShorthandSynthesizer, "_execute_llm", lambda self, m, s: mock_call(m, s))

    response = test_client.post(
        f"/api/recordings/{rid}/notes/shorthand",
        json={
            "raw_text": "前端联调下周一",
            "captured_at": 11.0,
            "synthesize": True,
            "speaker_hint": "李四",
        },
    )
    assert response.status_code == 201
    note_id = response.json()["id"]

    # Allow brief background task execution
    time.sleep(0.1)

    recordings = RecordingStore(test_client.app.state.store)
    saved = recordings.get_note(rid, note_id)
    assert saved is not None
    assert saved["status"] == "ready"
    assert "前端联调定于下周一启动" in saved["synthesized_text"]
    assert saved["anchor_start"] == 10.0


def test_shorthand_does_not_send_to_cloud_profile(test_client, monkeypatch):
    rid = create_mock_recording(test_client)
    assert test_client.post("/api/llm-configs", json={
        "name": "Cloud", "provider_type": "cloud", "base_url": "https://example.invalid/v1",
        "model": "cloud", "api_key": "fake", "is_default": True,
    }).status_code == 201
    from app.shorthand import ShorthandSynthesizer
    requests = []
    async def unexpected(*args):
        requests.append(args)
    monkeypatch.setattr(ShorthandSynthesizer, "_execute_llm", unexpected)
    result = test_client.post(f"/api/recordings/{rid}/notes/shorthand", json={
        "raw_text": "仅保存在本机", "synthesize": True,
    }).json()
    saved = test_client.app.state.services.recordings.get_note(rid, result["id"])
    assert saved["raw_text"] == "仅保存在本机"
    assert saved["status"] == "failed"
    assert not requests
