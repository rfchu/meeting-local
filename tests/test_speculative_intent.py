"""Tests for real-time speculative intent detector (decisions and action items)."""
import pytest
from app.speculative_intent import (
    SpeculativeCandidate,
    SpeculativeDetector,
    detect_candidates_from_segments,
)


def test_detect_decision_explicit():
    detector = SpeculativeDetector()
    text = "经过架构评审，我们决定把数据库隔离级别定为 Read Committed"
    candidates = detector.detect_text(text, speaker="张三", timestamp=14.5)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "decision"
    assert c.confidence >= 0.85
    assert "Read Committed" in c.content or "Read Committed" in c.title
    assert c.speaker == "张三"
    assert c.candidate_id


def test_detect_todo_with_owner_and_deadline():
    detector = SpeculativeDetector()
    text = "李四负责压测脚本，本周五前完成"
    candidates = detector.detect_text(text, speaker="主持人", timestamp=45.0)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "todo"
    assert c.owner == "李四"
    assert "周五" in c.due_date
    assert "压测脚本" in c.content or "压测脚本" in c.title
    assert c.confidence >= 0.85


def test_detect_todo_first_person():
    detector = SpeculativeDetector()
    text = "我明天把接口文档发给大家"
    candidates = detector.detect_text(text, speaker="王五", timestamp=75.0)

    assert len(candidates) == 1
    c = candidates[0]
    assert c.kind == "todo"
    assert c.owner == "我"
    assert "明天" in c.due_date
    assert "接口文档" in c.content or "接口文档" in c.title
    assert c.confidence >= 0.85


def test_ignore_chitchat_and_questions():
    detector = SpeculativeDetector()
    noisy_samples = [
        "好的，大家听得清楚吗？",
        "哈哈确实是这样",
        "收到，明白，好的",
        "喂喂能听到声音吗？",
        "这个方案到底定没定下来呢？",
        "今天天气真不错",
    ]
    for sample in noisy_samples:
        candidates = detector.detect_text(sample, speaker="未知", timestamp=10.0)
        assert len(candidates) == 0, f"Expected 0 candidates for noisy sample: '{sample}', got {candidates}"


def test_sliding_window_deduplication():
    detector = SpeculativeDetector()
    segments = [
        {"id": "seg-1", "text": "今天开会同步进度。", "start": 1.0, "end": 4.0, "speaker": "A"},
        {"id": "seg-2", "text": "经过讨论，大家一致确定采用方案B。", "start": 5.0, "end": 10.0, "speaker": "A"},
        {"id": "seg-3", "text": "好的，方案B确实比较稳。", "start": 11.0, "end": 14.0, "speaker": "B"},
        {"id": "seg-4", "text": "我周五前把部署手册输出给运维团队。", "start": 15.0, "end": 20.0, "speaker": "B"},
    ]

    # First detection window
    res1 = detect_candidates_from_segments(segments, detector=detector)
    assert len(res1) == 2
    kinds = {c.kind for c in res1}
    assert kinds == {"decision", "todo"}

    # Pass seen candidate IDs in second detection window (sliding window)
    seen_ids = {c.candidate_id for c in res1}
    res2 = detect_candidates_from_segments(segments, seen_ids=seen_ids, detector=detector)
    assert len(res2) == 0, "Already seen candidates should be filtered out"


def test_candidate_id_deterministic():
    detector = SpeculativeDetector()
    text = "我们决定采用微服务架构"
    c1 = detector.detect_text(text, speaker="架构师", timestamp=10.0)[0]
    c2 = detector.detect_text(text, speaker="架构师", timestamp=25.0)[0]
    assert c1.candidate_id == c2.candidate_id
