"""Small, read-only projection for the native companion window."""
import json
import math
import time

from .chinese_record import saved_translation, segment_language, translation_file
from .languages import recording_language


def snapshot(services, mode, rid=None):
    runtime = services.recording_runtime
    if rid:
        try:
            active = [runtime.read(rid)]
        except FileNotFoundError:
            active = []
    else:
        active = []
        for row in services.recordings.list():
            if row['state'] not in ('starting', 'recording', 'paused'):
                continue
            try:
                value = runtime.read(row['id'])
            except FileNotFoundError:
                continue
            if value['phase'] in ('starting', 'recording', 'paused', 'unknown'):
                active.append(value)
            if len(active) == 2:
                break
    phase = 'multiple' if len(active) > 1 else 'missing' if rid and not active else 'idle'
    result = {'observed_at': time.time(), 'phase': phase, 'lines': []}
    if len(active) != 1:
        return result
    value = active[0]
    rid = value['recording_id']
    result.update(recording_id=rid, meeting_id=value['identity']['meeting_id'], source=value['source'],
                  phase=value['phase'], storage=value['storage']['state'], asr=value['asr']['state'])
    result['duration_seconds'] = value['capture']['timeline_seconds']
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
        items.sort(key=lambda item: max(entry['start'] for entry in item['evidence']), reverse=True)
        result['lines'] = [item['content'][:350] for item in items[:3]]
    if mode == 'bilingual' and (engine := value['asr']['engine']):
        hint = recording_language(services.recordings, rid)
        rows = services.live_results.snapshot(rid, engine, hint)
        latest = max((segment for row in rows for segment in row['segments']), key=lambda s: s['end'], default=None)
        if latest:
            result['lines'] = [latest['text'][:500]]
            language = segment_language(latest['text'], hint)
            if language in ('en', 'ja'):
                with services.store.connect() as db:
                    providers = services.retrieval.translation_providers(db)
                for provider in providers:
                    path = translation_file(services.store.directory(rid), latest, language, provider)
                    if translation := saved_translation(path):
                        result['lines'].append(translation[:500])
                        break
                else:
                    result['lines'].append('等待工作台译文')
    return result
