"""Read-only publication receipt; ASR coverage uses the same plan as quick completion."""
from .live_results import plan_ranges, RATE
from .languages import recording_language


def completion_report(store, recordings, live_results, rid, engine):
    session=recordings.read(rid)
    meeting=store.one('SELECT duration FROM meetings WHERE id=?',(rid,))
    duration=float(meeting['duration'] if meeting else session.get('duration',0))
    original=store.directory(rid)/'original'
    size=original.stat().st_size if original.is_file() else 0
    rows=live_results.snapshot(rid,engine,recording_language(recordings,rid))
    reused,gaps=plan_ranges([row for row in rows if row['state']=='completed'],duration)
    covered=sum((row['end_frame']-row['start_frame'])/RATE for row in reused)
    attempts=store.all('SELECT state,COUNT(*) AS count FROM live_asr_ranges WHERE recording_id=? AND engine=? GROUP BY state',(rid,engine))
    states={row['state']:row['count'] for row in attempts}
    notes=store.one('SELECT COUNT(*) AS count FROM meeting_notes WHERE meeting_id=?',(rid,))['count'] if meeting else len(session.get('notes',[]))
    quality=recording_quality(recordings,rid,duration)
    return {'quality':quality,'published':bool(meeting),'audio_saved':bool(meeting and size),
            'saved_bytes':size,'duration_seconds':duration,'notes_count':notes,
            'coverage':{'engine':engine,'covered_seconds':round(covered,2),
                        'uncovered_seconds':round(sum(b-a for a,b in gaps),2),
                        'gaps':[{'start':a,'end':b} for a,b in gaps],
                        'completed_ranges':len(reused),'failed_attempts':states.get('failed',0)}}


def recording_quality(recordings,rid,duration,*,pending_only=False,offset=0,limit=20,gaps_only=False):
    if not (recordings.root/rid/'session.json').is_file():return {'recorded':False,'available':True,'monitored':False,'total_count':0,'items':[]}
    journal=recordings.lifecycle
    result=journal.anomalies(rid,limit=limit,pending_only=pending_only,offset=offset,gaps_only=gaps_only) if journal else {'available':False,'total_count':0,'items':[]}
    monitored=result.get('monitored',False)
    for item in result['items']:
        for key in ('start_seconds','end_seconds'):
            if item[key] is not None:item[key]=min(duration,max(0,item[key]))
    return dict(result,recorded=True,monitored=monitored)


def recording_quality_summary(recordings,rid,duration):
    result=recording_quality(recordings,rid,duration,limit=0)
    result.pop('items',None)
    return result
