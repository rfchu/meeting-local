"""Language policy shared by recording, ASR and editorial tasks."""
from typing import Literal

SourceLanguage = Literal['auto', 'zh', 'en', 'ja']
TranscriptionLanguage = SourceLanguage
EXPERIMENTAL_TRANSCRIPTION_LANGUAGES = {'ja': 'Japanese'}
OutputLanguage = Literal['zh', 'en']
ASR_NAMES = {'auto': None, 'zh': 'Chinese', 'en': 'English', 'ja': 'Japanese'}
AUTO_LANGUAGES = frozenset(('zh', 'en', 'Chinese', 'English'))


class UnsupportedAutoLanguage(ValueError):
    pass


def check_auto_languages(languages):
    if not languages or any(name not in AUTO_LANGUAGES for name in languages):
        raise UnsupportedAutoLanguage('自动识别仅支持中英文。日语会议请选择「日语（Beta）」后重新识别。')


def source_language(value=None):
    value = value or 'zh'  # Existing recordings and jobs used a fixed Chinese prompt.
    if value not in ASR_NAMES:
        raise ValueError('原文语言仅支持自动识别、中文、英文或日语')
    return value


def configure_asr(provider, language):
    provider.language = source_language(language)
    return provider


def configure_transcription_asr(provider, language):
    if language in EXPERIMENTAL_TRANSCRIPTION_LANGUAGES:
        if provider.engine_id != 'qwen':
            raise ValueError('实验性语言草稿仅支持千问会后转写')
        provider.language = language
        return provider
    return configure_asr(provider, language)


def identity_language(identity):
    if isinstance(identity, str):
        import json
        identity = json.loads(identity)
    if isinstance(identity, dict):
        identity = identity.get('model', [])
    return next((item['source_language'] for item in identity
                 if isinstance(item, dict) and 'source_language' in item), 'zh')


def meeting_languages(store, mid):
    import json
    row = store.one('SELECT payload FROM meeting_briefs WHERE meeting_id=?', (mid,))
    value = json.loads(row['payload']) if row else {}
    return {'source_language': source_language(value.get('source_language')),
            'output_language': value.get('output_language') or 'zh'}


def recording_language(recordings, rid, requested=None):
    if requested is not None:
        return source_language(requested)
    session = recordings.read(rid) if rid else {}
    return source_language(session.get('source_language') or (session.get('brief') or {}).get('source_language'))


def output_instruction(language):
    if language == 'en':
        return ('\nWrite the meeting title, narrative, conclusions and action descriptions in English. '
                'Preserve names, exact quotations, source IDs, dates, numbers and JSON keys. '
                'Translate meaning faithfully without adding facts. Do not translate cited source excerpts.')
    return '\n会议标题、叙述、结论和行动描述使用简体中文。保留人名、原文引文、来源 ID、日期、数字和 JSON 键，不新增事实。'


def qwen_text(raw, language):
    """Auto detection emits a language header before <asr_text>; never show it as speech."""
    if source_language(language) != 'auto':
        return raw.strip()
    header, marker, text = raw.partition('<asr_text>')
    return text.strip() if marker and header.removeprefix('language ').strip() in AUTO_LANGUAGES else ''
