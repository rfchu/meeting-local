"""End-to-end test for Foresight in-meeting copilot (shorthand, synthesis, speculative intent, acceptance)."""
import time
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.recordings import RecordingStore
from app import shorthand


@pytest.fixture
def test_client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        yield client


def test_copilot_full_lifecycle_e2e(test_client, monkeypatch):
    store = test_client.app.state.store
    recordings = RecordingStore(store)
    session = recordings.create("audio/webm")
    rid = session["id"]

    assert test_client.post("/api/llm-configs", json={
        "name": "测试", "provider_type": "local", "base_url": "http://127.0.0.1:1234/v1", "model": "test"
    }).status_code == 201
    monkeypatch.setattr(test_client.app.state.services.live_sessions, "snapshot", lambda _: {
        "segments": [{"start": 10, "end": 18, "text": "我们决定把数据库隔离级别定为 Read Committed。", "speaker": "张三"}]
    })

    # Mock synthesizer LLM response
    async def mock_synthesis(messages, schema):
        return {
            "summary": "确定数据库隔离级别定为 Read Committed",
            "details": "架构团队一致同意并计划周五完成压测",
            "speakers": ["张三"],
            "anchor_start": 10.0,
            "anchor_end": 18.0,
            "evidence_quote": "我们决定把数据库隔离级别定为 Read Committed。",
        }

    monkeypatch.setattr(
        shorthand.ShorthandSynthesizer,
        "_execute_llm",
        lambda self, m, s: mock_synthesis(m, s),
    )

    # 1. Post raw shorthand note (instant raw note path)
    resp_raw = test_client.post(
        f"/api/recordings/{rid}/notes/shorthand",
        json={
            "raw_text": "前端周五提测",
            "captured_at": 5.0,
            "synthesize": False,
        },
    )
    assert resp_raw.status_code == 201
    raw_note_id = resp_raw.json()["id"]
    assert resp_raw.json()["status"] == "raw"

    # 2. Post shorthand note with synthesize=True
    resp_syn = test_client.post(
        f"/api/recordings/{rid}/notes/shorthand",
        json={
            "raw_text": "db RC 压测周五",
            "captured_at": 14.0,
            "synthesize": True,
            "speaker_hint": "张三",
        },
    )
    assert resp_syn.status_code == 201
    syn_note_id = resp_syn.json()["id"]
    assert resp_syn.json()["status"] == "synthesizing"

    # Allow async task to complete
    time.sleep(0.3)
    updated_syn = recordings.get_note(rid, syn_note_id)
    assert updated_syn is not None
    assert updated_syn["status"] == "ready"
    assert "Read Committed" in updated_syn["synthesized_text"]

    # 3. Simulate ASR segments arriving
    live_segments = [
        {"id": "seg-1", "text": "经过评审，我们决定把数据库隔离级别定为 Read Committed。", "start": 10.0, "end": 15.0, "speaker": "张三"},
        {"id": "seg-2", "text": "李四负责压测脚本，本周五前完成。", "start": 16.0, "end": 20.0, "speaker": "李四"},
    ]
    live_sessions = test_client.app.state.services.live_sessions
    monkeypatch.setattr(live_sessions, "snapshot", lambda r: {"segments": live_segments})
    monkeypatch.setattr(live_sessions, "emit", lambda r, ev, **kw: None)

    # 4. Query speculative candidates
    candidates_resp = test_client.get(f"/api/recordings/{rid}/candidates")
    assert candidates_resp.status_code == 200
    candidates = candidates_resp.json()["candidates"]
    assert len(candidates) == 2

    c_decision = next(c for c in candidates if c["kind"] == "decision")
    c_todo = next(c for c in candidates if c["kind"] == "todo")

    # 5. Accept decision candidate (simulating Tab press)
    acc_resp = test_client.post(f"/api/recordings/{rid}/candidates/{c_decision['candidate_id']}/accept")
    assert acc_resp.status_code == 200
    assert acc_resp.json()["ok"] is True

    # 6. Reject todo candidate (simulating Esc press)
    rej_resp = test_client.post(f"/api/recordings/{rid}/candidates/{c_todo['candidate_id']}/reject")
    assert rej_resp.status_code == 200
    assert rej_resp.json()["ok"] is True

    # Check candidates query: 0 active remaining, 1 accepted, 1 dismissed
    query_after = test_client.get(f"/api/recordings/{rid}/candidates").json()
    assert len(query_after["candidates"]) == 0
    assert query_after["accepted_count"] == 1
    assert query_after["dismissed_count"] == 1

    # Verify session notes include the raw note, synthesized note, and accepted decision
    session_data = recordings.read(rid)
    notes = session_data.get("notes", [])
    assert len(notes) >= 3
    types = [n.get("note_type") for n in notes]
    assert "note" in types
    assert "decision" in types
