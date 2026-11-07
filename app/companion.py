"""Small, read-only projection for the native companion window."""
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
    result = {'observed_at': time.time(), 'phase': 'multiple' if len(active) > 1 else 'idle', 'lines': []}
    if len(active) != 1:
        return result
    value = active[0]
    rid = value['recording_id']
    result.update(recording_id=rid, meeting_id=value['identity']['meeting_id'], source=value['source'],
                  phase=value['phase'], storage=value['storage']['state'], asr=value['asr']['state'])
    if mode == 'highlights':
        items = services.live_overview.read(rid)['items']
        result['lines'] = [item['content'][:350] for item in items if not item['stale'] and item['decision'] != 'dismissed'][:3]
    if mode == 'bilingual' and (engine := value['asr']['engine']):
        hint = recording_language(services.recordings, rid)
        rows = services.live_results.snapshot(rid, engine, hint)
        latest = max((segment for row in rows for segment in row['segments']), key=lambda s: s['end'], default=None)
        if latest:
            result['lines'] = [latest['text'][:500]]
            if segment_language(latest['text'], hint) == 'en':
                with services.store.connect() as db:
                    providers = services.retrieval.translation_providers(db)
                for provider in providers:
                    path = translation_file(services.store.directory(rid), latest, 'en', provider)
                    if translation := saved_translation(path):
                        result['lines'].append(translation[:500])
                        break
                else:
                    result['lines'].append('等待工作台译文')
    return result
