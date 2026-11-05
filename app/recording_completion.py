"""Read-only publication receipt; ASR coverage uses the same plan as quick completion."""
from .live_results import plan_ranges, RATE


def completion_report(store, recordings, live_results, rid, engine):
    session=recordings.read(rid)
    meeting=store.one('SELECT duration FROM meetings WHERE id=?',(rid,))
    duration=float(meeting['duration'] if meeting else session.get('duration',0))
    original=store.directory(rid)/'original'
    size=original.stat().st_size if original.is_file() else 0
    reused,gaps=plan_ranges(live_results.snapshot(rid,engine),duration)
    covered=sum((row['end_frame']-row['start_frame'])/RATE for row in reused)
    attempts=store.all('SELECT state,COUNT(*) AS count FROM live_asr_ranges WHERE recording_id=? AND engine=? GROUP BY state',(rid,engine))
    states={row['state']:row['count'] for row in attempts}
    notes=store.one('SELECT COUNT(*) AS count FROM meeting_notes WHERE meeting_id=?',(rid,))['count'] if meeting else len(session.get('notes',[]))
    return {'published':bool(meeting),'audio_saved':bool(meeting and size),
            'saved_bytes':size,'duration_seconds':duration,'notes_count':notes,
            'coverage':{'engine':engine,'covered_seconds':round(covered,2),
                        'uncovered_seconds':round(sum(b-a for a,b in gaps),2),
                        'gaps':[{'start':a,'end':b} for a,b in gaps],
                        'completed_ranges':len(reused),'failed_attempts':states.get('failed',0)}}
