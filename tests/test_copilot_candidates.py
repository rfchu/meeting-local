"""Tests for speculative intent candidates API (detect, accept, reject)."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.recordings import RecordingStore
from app.speculative_intent import SpeculativeCandidate


@pytest.fixture
def test_client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        yield client


def create_mock_recording(client):
    store = client.app.state.store
    recordings = RecordingStore(store)
    session = recordings.create("audio/webm")
    return session["id"]


def test_candidates_query_empty_by_default(test_client):
    rid = create_mock_recording(test_client)
    res = test_client.get(f"/api/recordings/{rid}/candidates")
    assert res.status_code == 200
    data = res.json()
    assert "candidates" in data
    assert data["candidates"] == []


def test_candidates_accept_and_reject(test_client, monkeypatch):
    rid = create_mock_recording(test_client)

    # Mock live_sessions snapshot to return segments containing a decision and a todo
    segments = [
        {"id": "seg-1", "text": "架构评审完毕，大家一致决定采用方案A。", "start": 10.0, "end": 15.0, "speaker": "张三"},
        {"id": "seg-2", "text": "李四负责压测脚本，本周五前完成。", "start": 16.0, "end": 20.0, "speaker": "李四"},
    ]

    live_sessions = test_client.app.state.services.live_sessions
    monkeypatch.setattr(live_sessions, "snapshot", lambda r: {"segments": segments})
    monkeypatch.setattr(live_sessions, "emit", lambda r, event_type, **kwargs: None)

    # 1. Query candidates (should detect both the decision and the todo)
    res = test_client.get(f"/api/recordings/{rid}/candidates")
    assert res.status_code == 200
    candidates = res.json()["candidates"]
    assert len(candidates) == 2

    c_decision = next(c for c in candidates if c["kind"] == "decision")
    c_todo = next(c for c in candidates if c["kind"] == "todo")

    # 2. Accept the decision candidate
    accept_res = test_client.post(f"/api/recordings/{rid}/candidates/{c_decision['candidate_id']}/accept")
    assert accept_res.status_code == 200
    assert accept_res.json()["ok"] is True
    assert accept_res.json()["status"] == "accepted"

    # Verify decision is added to session notes
    recordings = RecordingStore(test_client.app.state.store)
    session = recordings.read(rid)
    note_types = [n.get("note_type") for n in session.get("notes", [])]
    assert "decision" in note_types

    # 3. Reject (dismiss) the todo candidate
    reject_res = test_client.post(f"/api/recordings/{rid}/candidates/{c_todo['candidate_id']}/reject")
    assert reject_res.status_code == 200
    assert reject_res.json()["ok"] is True
    assert reject_res.json()["status"] == "dismissed"

    # 4. Query candidates again -> both should now be excluded from active candidates!
    res_after = test_client.get(f"/api/recordings/{rid}/candidates")
    assert res_after.status_code == 200
    active = res_after.json()["candidates"]
    assert len(active) == 0
    assert res_after.json()["accepted_count"] == 1
    assert res_after.json()["dismissed_count"] == 1


def test_candidate_not_found(test_client):
    rid = create_mock_recording(test_client)
    res = test_client.post(f"/api/recordings/{rid}/candidates/nonexistent-cid/accept")
    assert res.status_code == 404


def test_live_todo_is_published_once_with_owner_and_due_date(test_client, monkeypatch):
    rid = create_mock_recording(test_client)
    services = test_client.app.state.services
    monkeypatch.setattr(services.live_sessions, "snapshot", lambda _: {"segments": [
        {"text": "李四负责压测脚本，本周五前完成。", "start": 4, "end": 8,
         "source_track": "microphone", "live_range_id": "range", "live_segment_index": 1},
        {"text": "我们还没有决定采用新的方案。", "start": 8, "end": 12},
    ]})
    route = f"/api/recordings/{rid}"
    candidates = test_client.get(route + "/candidates").json()["candidates"]
    assert [c["kind"] for c in candidates] == ["todo"]
    assert candidates[0]["anchor_segments"] == ["range:1"]
    path = route + f"/candidates/{candidates[0]['candidate_id']}/accept"
    first = test_client.post(path).json()["note"]
    assert test_client.post(path).json()["note"]["id"] == first["id"]
    services.recordings.metadata(rid, [], 12, 1)
    services.recordings.chunk(rid, 0, b"synthetic audio")
    services.recordings.finish(rid, 1)
    meeting = test_client.get(f"/api/meetings/{rid}").json()
    [todo] = meeting["note_todos"]
    assert todo["note_id"] == first["id"]
    assert next(n for n in meeting["notes"] if n["id"] == first["id"])["shorthand_track"] == "microphone"
    assert todo["owner"] == "李四" and "周五" in todo["due_date"]
