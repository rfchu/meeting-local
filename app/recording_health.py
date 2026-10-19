"""Read-only, allowlisted recording inventory. Never export content or raw errors."""
import json
import math
import time


def number(value):
    try:
        value=float(value)
        return max(0,value) if math.isfinite(value) else 0
    except (TypeError,ValueError):return 0


def file_bytes(path):
    try:return path.stat().st_size
    except FileNotFoundError:return 0


class RecordingHealth:
    def __init__(self,store,recordings,native):
        self.store=store;self.recordings=recordings;self.native=native

    def snapshot(self,rid):
        session=self.recordings.read(rid);folder=self.recordings.folder(rid)
        native=session.get('source')=='macos'
        completed=bool(self.store.one('SELECT id FROM meetings WHERE id=?',(rid,))) or session.get('state')=='completed'
        running=native and self.native.is_running(rid)
        capture={}
        if native:
            try:capture=json.loads((folder/'capture.json').read_text())
            except (OSError,ValueError):pass
            if not isinstance(capture,dict):capture={}
        duration=number(capture.get('duration',session.get('duration',0)))
        parts={int(p.stem):file_bytes(p) for p in folder.glob('*.part') if p.stem.isdigit() and 0<=int(p.stem)<100000}
        expected=min(100000,max(int(number(session.get('chunk_count'))),max(parts,default=-1)+1))
        missing=[i for i in range(expected) if not parts.get(i)]
        contiguous=0
        while parts.get(contiguous):contiguous+=1
        tracks=[]
        for name in ('system','microphone') if native else ():
            path=folder/(name+'.pcm');size=file_bytes(path)
            tracks.append({'name':name,'saved_bytes':size,'saved_seconds':round(size/32000,2)})
        saved_bytes=sum(parts.values())+sum(t['saved_bytes'] for t in tracks)
        # Published original files supersede chunks removed after a successful finish.
        original=self.store.directory(rid)/'original'
        if completed and original.exists():saved_bytes=file_bytes(original)
        states={'recording','paused','starting','stopped','failed','interrupted','completed'}
        state='completed' if completed else capture.get('state',session.get('state'))
        if native and not running and state in ('recording','paused','starting'):state='interrupted'
        if not isinstance(state,str) or state not in states:state='unknown'
        asr={}
        for engine in ('whisper','qwen'):
            rows=self.store.all('SELECT start_frame,end_frame,state FROM live_asr_ranges WHERE recording_id=? AND engine=?',(rid,engine))
            cursor=0;covered=0
            for row in sorted((r for r in rows if r['state']=='completed'),key=lambda r:r['start_frame']):
                start=max(0,row['start_frame']);end=min(duration*16000,row['end_frame'])
                if end>max(start,cursor):covered+=end-max(start,cursor)
                cursor=max(cursor,end)
            asr[engine]={'covered_seconds':round(covered/16000,2),
                         'failed_attempts':sum(r['state']=='failed' for r in rows),
                         'running_attempts':sum(r['state']=='running' for r in rows)}
        warnings=[]
        if not saved_bytes:warnings.append('no_saved_audio')
        if missing and not native and not completed:warnings.append('missing_chunks')
        if native and not running and not completed:warnings.append('capture_not_running')
        return {'schema_version':1,'source':'native' if native else 'browser','state':state,
                'duration_seconds':round(duration,2),'native_running':bool(running),
                'metadata_age_seconds':round(max(0,time.time()-number(session.get('updated'))),1),
                'audio':{'saved_bytes':saved_bytes,'saved_chunks':sum(size>0 for size in parts.values()),
                         'expected_chunks':expected,'missing_chunk_count':len(missing) if not completed else 0,
                         'missing_chunk_indices':missing[:50] if not completed else [],
                         'contiguous_chunks':contiguous,'tracks':tracks,
                         'recoverable':not completed and not running and saved_bytes>0 and (native or not missing)},
                'notes_count':len(session.get('notes',[])), 'asr':asr,'warning_codes':warnings}
