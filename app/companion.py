"""Small, read-only projection for the native companion window."""
import json
import math
import time

from .chinese_record import saved_translation, segment_language, translation_file
from .languages import recording_language


def snapshot(services, mode, rid=None):
    automatic = rid is None
    runtime = services.recording_runtime
    if rid:
        try:
            active = [runtime.read(rid)]
        except FileNotFoundError:
            active = []
    else:
        active = []
        uncertain = []
        for row in services.recordings.list():
            if row['state'] not in ('starting', 'recording', 'paused'):
                continue
            try:
                value = runtime.read(row['id'])
            except FileNotFoundError:
                continue
            if value['phase'] in ('starting', 'recording', 'paused'):
                active.append(value)
            elif value['phase'] == 'unknown':
                uncertain.append(value)
            if len(active) == 2:
                break
        if not active:
            active = uncertain
    phase = 'multiple' if len(active) > 1 else 'missing' if rid and not active else 'idle'
    result = {'observed_at': time.time(), 'phase': phase, 'lines': []}
    if len(active) != 1:
        return result
    value = active[0]
    rid = value['recording_id']
    result.update(recording_id=rid, meeting_id=value['identity']['meeting_id'], source=value['source'],
                  phase=value['phase'], storage=value['storage']['state'], asr=value['asr']['state'])
    result['duration_seconds'] = value['capture']['timeline_seconds']
    if automatic and value['phase'] == 'unknown':
        return result
    if value['source'] == 'native' and value['phase'] == 'recording':
        try:
            report = json.loads((services.recordings.folder(rid)/'capture.json').read_text())
            if report.get('state') == 'recording' and abs(time.time() - float(report['updated'])) <= 4:
                for track in ('system', 'microphone'):
                    levels = report.get(track + '_levels', [])
                    if isinstance(levels, list) and levels:
                        age = report.get('buffer_age', {}).get(track)
                        result[track + '_levels'] = [
                            min(1.0, max(0.0, level)) if type(level) in (int, float)
                            and math.isfinite(level) and isinstance(age, (int, float)) and age <= 1 else 0.0
                            for level in levels[-24:]
                        ]
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if mode == 'highlights':
        items = [item for item in services.live_overview.read(rid)['items']
                 if not item['stale'] and item['decision'] != 'dismissed']
        items.sort(key=lambda item: max((entry['start'] for entry in item['evidence']), default=0), reverse=True)
        result['lines'] = [item['content'][:350] for item in items[:3]]
    if mode in ('original', 'bilingual') and (engine := value['asr']['engine']):
        result.update(subtitles(services, rid, engine, mode == 'bilingual'))
    return result


def subtitles(services, rid, engine, bilingual):
    hint = recording_language(services.recordings, rid)
    live = services.store.one('SELECT seq,partial,status FROM live_sessions WHERE recording_id=?', (rid,))
    if live and live['status'] == 'stopped':
        live = None
    partial = json.loads(live['partial']) if live else {}
    rows = services.live_results.snapshot(rid, engine, hint)
    confirmed = [(row.get('id'), index, segment) for row in rows
                 for index, segment in enumerate(row['segments'])]
    latest = max(confirmed, key=lambda item: item[2]['end'], default=None)
    # A final can be stored just before the supervisor clears its preview checkpoint.
    track_ends = {}
    for _, _, source in confirmed:
        track = source.get('source_track')
        track_ends[track] = max(track_ends.get(track, 0), source['end'])
    drafts = []
    for part in partial.get('tracks') or [partial]:
        if not part.get('text') or part.get('end', 0) <= track_ends.get(part.get('source_track'), 0):
            continue
        text = part['text'][-500:]
        stable = part.get('stable_text', '')
        if not part['text'].startswith(stable):
            stable = ''
        omitted = len(part['text']) - len(text)
        drafts.append({'text': text, 'stable_text': stable[omitted:omitted + len(text)],
                       'source_track': part.get('source_track')})
    result = {'subtitle_seq': live['seq'] if live else None, 'drafts': drafts, 'lines': []}
    if not latest:
        return result
    aid, index, source = latest
    result['lines'] = [source['text'][:500]]
    language = segment_language(source['text'], hint)
    if bilingual and language in ('en', 'ja'):
        with services.store.connect() as db:
            providers = services.retrieval.translation_providers(db)
        for provider in providers:
            path = translation_file(services.store.directory(rid), source, language, provider)
            if translation := saved_translation(path):
                result['lines'].append(translation[:500])
                break
        else:
            result['lines'].append('等待译文' if providers else '请在运行管理配置本地翻译模型')
            if providers and aid:
                preferred = next((provider for provider in providers if provider.profile.get('is_default')), providers[0])
                result['translation_request'] = {'aid': aid, 'index': index,
                                                 'profile_id': preferred.profile['id']}
    return result
