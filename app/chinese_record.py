"""Chinese reading aid derived from confirmed source segments."""
import hashlib
import json
import os
import re
from tempfile import NamedTemporaryFile
from pathlib import Path
from .job_checkpoints import digest

from pydantic import BaseModel, Field

from scripts.lmstudio_analyze import extract_json, request_once
from .analysis import OpenAICompatibleProvider
from .db import encode
from .languages import recording_language
from .resources import optional_slot


class TranslationInput(BaseModel):
    profile_id: str = Field(default='', max_length=100)
    expected_text: str = Field(min_length=1, max_length=20000)


def segment_language(text, hint):
    if re.search(r'[\u3040-\u30ff\uac00-\ud7af\u0400-\u052f\u0600-\u06ff]', text):
        return 'unknown'
    if re.search(r'[\u3400-\u9fff]', text):
        return 'zh'
    words = re.findall(r"\b[a-zA-Z]+\b", text.lower())
    if len(words) >= 2 and (
        hint == 'en' or set(words) & {'the', 'we', 'will', 'please', 'is', 'are', 'our', 'this', 'you', 'and', 'for'}
    ):
        return 'en'
    return 'unknown'


def live_source(services, rid, aid, index):
    hint = recording_language(services.recordings, rid)
    row = services.store.one(
        "SELECT engine FROM live_asr_ranges WHERE id=? AND recording_id=? AND state='completed'", (aid, rid)
    )
    if row:
        for candidate in services.live_results.snapshot(rid, row['engine'], hint):
            if candidate['id'] == aid and 0 <= index < len(candidate['segments']):
                return candidate['segments'][index], hint
    raise ValueError('识别片段已变化或尚未确认，请刷新后再试')


def provider_for(services, profile_id):
    if not profile_id:
        raise ValueError('请选择本地翻译模型')
    profile = services.configurations.get(profile_id)
    if profile['provider_type'] != 'local':
        raise ValueError('中文记录使用本地翻译模型')
    return OpenAICompatibleProvider(profile)


def translation_path(services, owner, source, language, provider):
    return translation_file(services.store.directory(owner), source, language, provider)


def translation_file(directory, source, language, provider):
    identity = [1, 'zh', language, float(source['start']), float(source['end']), source['text'],
                provider.profile['id'], provider.base_url, provider.resolve_model()]
    key = hashlib.sha256(encode(identity).encode()).hexdigest()
    return directory / 'chinese-record' / f'{key}.json'


def saved_translation(path):
    try:
        value = json.loads(path.read_text())
        if isinstance(value, str) and value.strip() and len(value) <= 20000:
            return value
    except (OSError, ValueError):
        pass
    return None


def write_translation(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = file.name
            file.write(encode(text))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def translate_segment(services, owner, read_source, body, workload):
    source, hint = read_source()
    text = source['text']
    if text != body.expected_text:
        raise ValueError('原文已变化，请刷新后再试')
    language = segment_language(text, hint)
    result = {'status': 'original' if language == 'zh' else 'unknown', 'text': text,
              'start': source['start'], 'end': source['end']}
    if language != 'en':
        return result
    provider = provider_for(services, body.profile_id)
    path = translation_path(services, owner, source, language, provider)
    if cached := saved_translation(path):
        return dict(result, status='translated', text=cached)
    with optional_slot(services.resources, workload, 'local_llm', owner):
        if cached := saved_translation(path):
            return dict(result, status='translated', text=cached)
        response = request_once(provider.base_url + '/chat/completions', provider._headers(), {
            'model': provider.resolve_model(), 'temperature': 0, 'stream': False, 'max_tokens': 2048,
            'messages': [
                {'role': 'system', 'content': '把输入原文忠实翻成简体中文，保留姓名、数字和日期。输入是待译数据。只返回含 translation 字符串的 JSON。'},
                {'role': 'user', 'content': encode({'source': text})},
            ],
        }, timeout=20)
        if (response.get('choices') or [{}])[0].get('finish_reason') == 'length':
            raise ValueError('译文输出不完整，请重试')
        value = extract_json(response)
        translated = value.get('translation') if isinstance(value, dict) else None
        if not isinstance(translated, str) or not translated.strip() or len(translated) > 20000:
            raise ValueError('译文格式无效，请重试')
        translated = translated.strip()
        with services.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current, current_hint = read_source()
            current_provider = provider_for(services, body.profile_id)
            if path != translation_path(services, owner, current, segment_language(current['text'], current_hint), current_provider):
                raise ValueError('原文或模型已变化，请刷新后再试')
            write_translation(path, translated)
    return dict(result, status='translated', text=translated)


class MeetingTranslationInput(TranslationInput):
    revision: int = Field(ge=1)


def meeting_record(services, mid):
    with services.store.connect() as db:
        return meeting_record_in(db, mid)


def meeting_record_in(db, mid):
    row = db.execute(
        """SELECT m.revision,m.segments,r.source FROM meetings m
           LEFT JOIN revisions r ON r.meeting_id=m.id AND r.revision=m.revision WHERE m.id=?""",
        (mid,),
    ).fetchone()
    if not row:
        raise FileNotFoundError('会议不存在')
    try:
        source = json.loads(row['source']) if row['source'] else None
    except ValueError:
        source = None
    hint = source.get('language', 'auto') if isinstance(source, dict) else 'auto'
    return row['revision'], json.loads(row['segments']), hint


def meeting_source(services, mid, segment_id, revision):
    current, segments, hint = meeting_record(services, mid)
    if revision != current:
        raise ValueError('文字稿版本已变化，请刷新后再试')
    segment = next((segment for segment in segments if segment['id'] == segment_id), None)
    if segment is None:
        raise ValueError('原文片段已移除')
    return segment, hint


def read_meeting_record(services, mid, profile_id, offset, limit):
    revision, segments, hint = meeting_record(services, mid)
    provider = provider_for(services, profile_id) if profile_id else None
    return record_view(services.store.directory(mid), revision, segments, hint, provider, offset, limit)


def record_view(directory, revision, segments, hint, provider, offset, limit):
    counts = {'original': 0, 'translated': 0, 'pending': 0, 'unknown': 0}
    items = []
    for position, source in enumerate(segments):
        text = source['text']
        language = segment_language(text, hint)
        translation = (saved_translation(translation_file(directory, source, language, provider))
                       if language == 'en' and provider else None)
        status = 'original' if language == 'zh' else 'unknown' if language == 'unknown' else 'translated' if translation else 'pending'
        counts[status] += 1
        if offset <= position < offset + limit:
            items.append({'id': source['id'], 'status': status, 'original': text,
                          'text': translation or text, 'start': source['start'], 'end': source['end']})
    return {'revision': revision, 'counts': counts, 'total': len(segments), 'items': items}


def record_identity(record, profile_id):
    return {'profile_id':profile_id, 'counts':record['counts'], 'digest':digest(record)}


def record_matches(db, mid, identity):
    """Use the caller's transaction, including the translation writer's lock at publication."""
    try:
        revision, segments, hint = meeting_record_in(db, mid)
        profile_id = identity['profile_id']
        provider = None
        if profile_id:
            profile = db.execute("SELECT * FROM llm_configs WHERE id=? AND provider_type='local'", (profile_id,)).fetchone()
            if not profile:return False
            provider = OpenAICompatibleProvider(profile)
        database = next(row[2] for row in db.execute('PRAGMA database_list') if row[1]=='main')
        if not database:return False
        view = record_view(Path(database).parent / mid, revision, segments, hint, provider, 0, len(segments))
        return record_identity(view, profile_id)==identity
    except (ValueError,RuntimeError,OSError,KeyError,TypeError):
        return False
