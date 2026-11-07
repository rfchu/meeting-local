"""Language routing and provenance checks with isolated data and synthetic providers."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import Store, uid
from app.languages import configure_asr, identity_language, qwen_text
from app.live_results import LiveResults
from app.main import create_app
from app.minutes import MeetingMinutes, MinutesItem, minutes_docx, minutes_markdown, set_output_language
from app.providers import QwenMLX, WhisperCpp, checkpoint_identity_matches
from app.recordings import RecordingStore


@pytest.mark.parametrize('language', ['auto', 'zh', 'en'])
def test_engines_receive_language_and_identity_prevents_wrong_reuse(tmp_path, monkeypatch, language):
    monkeypatch.setattr(WhisperCpp, 'ready', lambda self: True)
    whisper = configure_asr(WhisperCpp(), language)
    command = whisper.live_command(tmp_path/'a.wav', tmp_path/'out')
    assert command[command.index('-l') + 1] == language
    qwen = configure_asr(QwenMLX(), language)
    command = qwen.stream_command(tmp_path/'a.wav', tmp_path/'out')
    assert command[command.index('--language') + 1] == language
    assert identity_language(qwen.identity()) == language
    if language != 'zh':
        assert not checkpoint_identity_matches('qwen', QwenMLX().identity(), qwen.identity())


def test_durable_live_snapshot_filters_language_including_context_identity(tmp_path):
    store = Store(tmp_path)
    recordings = RecordingStore(store)
    rid = recordings.create('audio/webm')['id']
    cache = LiveResults(store, recordings)
    for language, identity in [('zh', []), ('en', [{'source_language': 'en'}]),
                               ('auto', {'model': [{'source_language': 'auto'}], 'context': 'hint'})]:
        aid, _ = cache.begin(rid, 'browser', 'qwen', 0, 1, b'pcm', identity)
        cache.complete(aid, [{'start': 0, 'end': 1, 'text': language}])
    for language in ('zh', 'en', 'auto'):
        rows = cache.snapshot(rid, 'qwen', language)
        assert len(rows) == 1 and rows[0]['segments'][0]['text'] == language


def test_auto_stream_hides_language_header_without_altering_english():
    assert qwen_text('language Engl', 'auto') == ''
    assert qwen_text('language English<asr_text>Hello world.', 'auto') == 'Hello world.'
    assert qwen_text('language Chinese<asr_text>你好。', 'auto') == '你好。'
    assert qwen_text('Hello world.', 'en') == 'Hello world.'


def test_preparation_and_asr_job_preserve_independent_languages(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperCpp, 'ready', lambda self: True)
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        body = {'title': 'Planning', 'source_language': 'auto', 'output_language': 'en'}
        assert client.put('/api/meeting-preparation', json=body).status_code == 200
        assert client.get('/api/meeting-preparation').json()['source_language'] == 'auto'
        mid = uid()
        app.state.store.directory(mid).mkdir()
        app.state.store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)', (mid, 'Planning', 'fixture.wav'))
        assert client.put(f'/api/meetings/{mid}/brief', json=body).status_code == 200
        response = client.post(f'/api/meetings/{mid}/jobs', json={'source_language': 'en'})
        assert response.status_code == 201, response.text
        config = json.loads(app.state.store.one('SELECT config FROM jobs WHERE id=?', (response.json()['id'],))['config'])
        assert config['source_language'] == 'en'
        assert identity_language(config['engine_identity']) == 'en'
        detail = client.get(f'/api/meetings/{mid}').json()
        assert detail['flow']['brief']['output_language'] == 'en'
        assert detail['flow']['brief']['source_language'] == 'en'
        for value in ('fr', 'English', 'mixed'):
            assert client.put('/api/meeting-preparation', json={'source_language': value}).status_code == 422


def test_language_preferences_do_not_make_existing_minutes_stale(tmp_path):
    from app.meeting_flow import ai_context, save_brief
    store = Store(tmp_path)
    mid = uid()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)', (mid, '旧会议', 'a.wav'))
    with store.connect() as db:
        _, before = ai_context(db, mid)
        save_brief(db, mid, {'source_language': 'en', 'output_language': 'zh'})
        _, after = ai_context(db, mid)
        assert before == after


def test_english_export_localizes_labels_but_keeps_original_quote():
    from io import BytesIO
    from docx import Document
    document = set_output_language(MeetingMinutes(
        title='Planning会议纪要', style='ai', template_version=2, meeting_date='2026-11-07',
        purpose='Plan delivery', action_items=[MinutesItem(content='Ship on Friday', source_segment_ids=['s1'])],
    ), 'en')
    segments = [{'id': 's1', 'start': 0, 'text': '周五交付', 'speaker': '甲'}]
    md = minutes_markdown(document, segments)
    assert 'Action Items' in md and 'Ship on Friday' in md and '待开始' not in md
    word = Document(BytesIO(minutes_docx(document, segments, True)))
    paragraphs = '\n'.join(p.text for p in word.paragraphs)
    cells = '\n'.join(cell.text for table in word.tables for row in table.rows for cell in row.cells)
    assert 'Meeting Information' in paragraphs and 'Source References' in paragraphs
    assert '周五交付' in cells and document.output_language == 'en'


def test_ai_minutes_language_instruction_preserves_source_aliases(tmp_path):
    from app.analysis import LMStudioProvider
    captured = {}
    response = {'basic_info': {'topic': 'Planning', 'meeting_form': '', 'attendees': [], 'purpose': '', 'source_segment_ids': ['s1']},
                'core_content': [{'content': 'Deliver on Friday.', 'source_segment_ids': ['s1']}],
                'action_items': [], 'summary': []}

    def run(command, extra=None):
        request = Path(command[command.index('--request') + 1])
        captured.update(json.loads(request.read_text()))
        Path(command[command.index('--output') + 1]).write_text(json.dumps(response))

    provider = LMStudioProvider()
    result = provider.generate_ai_minutes('synthetic', [{'id': 'original-id', 'start': 0, 'end': 3, 'speaker': 'Alice', 'text': 'Deliver on Friday.'}],
                                         {'output_language': 'en'}, tmp_path/'minutes', run)
    assert 'Write the meeting title' in captured['messages'][0]['content']
    assert 'in English' in captured['messages'][0]['content']
    assert result.core_content[0].source_segment_ids == ['original-id']


def test_editorial_job_freezes_output_language(tmp_path, monkeypatch):
    from app.analysis import AIMinutesResult, AIMinutesBasicInfo, AnalysisItem

    seen = []
    class Provider:
        base_url = 'http://127.0.0.1:1234/v1'
        def resolve_model(self, model=None):
            return 'synthetic'
        def generate_ai_minutes(self, model, segments, metadata, work, run):
            seen.append(metadata)
            return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='Delivery', source_segment_ids=['s']),
                                   core_content=[AnalysisItem(content='Delivery is planned.', source_segment_ids=['s'])])
    monkeypatch.setattr('app.main.LMStudioProvider', Provider)
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        store = app.state.store
        mid = uid()
        store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)', (mid, 'Delivery', 'test.wav'))
        store.save_revision(mid, [{'id': 's', 'start': 0, 'end': 1, 'speaker': 'Alice', 'text': 'Delivery is planned.'}], 'manual')
        path = f'/api/meetings/{mid}'
        client.put(path+'/brief', json={'source_language': 'en', 'output_language': 'en'})
        result = client.post(path+'/minutes/jobs', json={})
        assert result.status_code == 202, result.text
        jid = result.json()['job_id']
        config = json.loads(store.one('SELECT config FROM jobs WHERE id=?', (jid,))['config'])
        assert config['settings']['output_language'] == 'en'
        # Changing the default affects the next generation, not this queued job.
        client.put(path+'/brief', json={'source_language': 'en', 'output_language': 'zh'})
        store.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
        app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?', (jid,)))
        document = client.get(path).json()['artifacts'][0]['payload']
        assert document['output_language'] == 'en'
        assert seen[0]['composition']['output_language'] == 'en'
        assert not document['title'].endswith('会议纪要')
