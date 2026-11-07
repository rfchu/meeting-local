"""Fallback speaker attribution for transcripts with independent audio tracks."""

TRACK_SPEAKERS = {'system': '线上发言', 'microphone': '现场发言'}
UNKNOWN_SPEAKERS = frozenset(('', '未分配', *TRACK_SPEAKERS.values()))


def with_track_speaker(segment):
    label = TRACK_SPEAKERS.get(segment.get('source_track'))
    speaker = (segment.get('speaker') or '').strip()
    if label and speaker in ('', '未分配') and not segment.get('speaker_locked'):
        return dict(segment, speaker=label)
    return segment


def known_speakers(segments):
    names = ((segment.get('speaker') or '').strip() for segment in segments)
    return list(dict.fromkeys(name for name in names if name not in UNKNOWN_SPEAKERS))
