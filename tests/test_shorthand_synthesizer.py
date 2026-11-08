"""Unit tests for shorthand context extraction and synthesizer."""
import pytest
from app.shorthand import extract_context_window, ShorthandSynthesizer


def test_extract_context_window_normal():
    segments = [
        {'id': 's1', 'start': 0.0, 'end': 5.0, 'text': '大家早上好，开始开会。', 'speaker': '主持人'},
        {'id': 's2', 'start': 10.0, 'end': 15.0, 'text': '关于数据库架构，建议使用 Read Committed。', 'speaker': '张三'},
        {'id': 's3', 'start': 16.0, 'end': 20.0, 'text': '本周五之前必须完成高并发压测。', 'speaker': '张三'},
        {'id': 's4', 'start': 45.0, 'end': 50.0, 'text': '下一个议题是前端组件库。', 'speaker': '李四'},
    ]

    # Captured at 18.0s: window [0, 28] should match s2 and s3
    matched = extract_context_window(segments, captured_at=18.0, before=10.0, after=5.0)
    assert len(matched) == 2
    assert matched[0]['id'] == 's2'
    assert matched[1]['id'] == 's3'


def test_extract_context_window_early_in_meeting():
    segments = [
        {'id': 's1', 'start': 2.0, 'end': 6.0, 'text': '第一点，本周发版。', 'speaker': '王五'},
    ]
    # Note at 3.0s, before=30.0: should clamp start to 0 and match s1
    matched = extract_context_window(segments, captured_at=3.0, before=30.0, after=10.0)
    assert len(matched) == 1
    assert matched[0]['id'] == 's1'


def test_extract_context_window_empty_fallback():
    # If segments list is empty
    assert extract_context_window([], captured_at=20.0) == []


def test_synthesizer_success_with_mock_provider():
    import asyncio

    async def run_test():
        async def mock_call(messages, schema):
            return {
                'summary': '数据库架构确定采用 Read Committed',
                'details': '张三提议并在周五前完成高并发压测',
                'speakers': ['张三'],
                'anchor_start': 10.0,
                'anchor_end': 20.0,
                'evidence_quote': '建议使用 Read Committed。',
            }

        synthesizer = ShorthandSynthesizer(llm_call=mock_call)
        segments = [
            {'id': 's2', 'start': 10.0, 'end': 15.0, 'text': '建议使用 Read Committed。', 'speaker': '张三'},
            {'id': 's3', 'start': 16.0, 'end': 20.0, 'text': '本周五之前必须完成高并发压测。', 'speaker': '张三'},
        ]

        result = await synthesizer.synthesize(
            raw_text='db RC 压测周五',
            captured_at=18.0,
            segments=segments,
        )

        assert result['status'] == 'ready'
        assert 'Read Committed' in result['summary']
        assert result['anchor_start'] == 10.0
        assert result['anchor_end'] == 15.0
        assert result['speakers'] == ['张三']
        assert '【数据库架构确定采用 Read Committed】' in result['synthesized_text']

    asyncio.run(run_test())


def test_synthesizer_graceful_failure_on_error():
    import asyncio

    async def run_test():
        async def failing_call(messages, schema):
            raise RuntimeError('Metal memory exhausted')

        synthesizer = ShorthandSynthesizer(llm_call=failing_call)
        result = await synthesizer.synthesize(
            raw_text='预算减半',
            captured_at=50.0,
            segments=[],
        )

        # Should not raise exception; must gracefully fallback to raw text and status failed
        assert result['status'] == 'failed'
        assert result['summary'] == '预算减半'
        assert result['synthesized_text'] == '预算减半'
        assert 'Metal memory exhausted' in result['error']

    asyncio.run(run_test())


def test_cancelled_waiter_keeps_model_lease_until_http_returns(monkeypatch):
    import asyncio
    import io
    import threading
    from types import SimpleNamespace
    import urllib.request
    from app.resources import ResourceScheduler

    entered = threading.Event()
    release = threading.Event()

    def blocked_open(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: SimpleNamespace(open=blocked_open))
    provider = SimpleNamespace(base_url="http://127.0.0.1:1234/v1", resolve_model=lambda: "test")
    scheduler = ResourceScheduler()
    synth = ShorthandSynthesizer(llm_provider=provider, resources=scheduler)

    async def check():
        task = asyncio.create_task(synth._execute_llm([], {}))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert scheduler.admission_reason("live_shorthand", {"local_llm": 1}) == "local_llm_busy"
        finally:
            release.set()

    asyncio.run(check())
    assert scheduler.admission_reason("live_shorthand", {"local_llm": 1}) == ""


@pytest.mark.parametrize("same_window", [False, True])
def test_duplicate_quote_only_resolves_to_a_unique_evidence_window(same_window):
    import asyncio

    async def model(*_):
        return {
            "summary": "确认周五交付", "details": "",
            "evidence_quote": "周五交付", "anchor_start": 10, "anchor_end": 12,
        }

    segments = [
        {"text": "周五交付", "source_track": "system",
         "start": 10 if same_window else 4, "end": 12 if same_window else 6},
        {"text": "周五交付", "source_track": "microphone", "start": 10, "end": 12},
    ]
    result = asyncio.run(ShorthandSynthesizer(llm_call=model).synthesize("交付", 10, segments))
    if same_window:
        assert result["status"] == "failed"
        assert result["synthesized_text"] == "交付"
        assert not result["evidence_quote"]
    else:
        assert result["status"] == "ready"
        assert result["source_track"] == "microphone"
