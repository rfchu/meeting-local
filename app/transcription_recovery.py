"""Expose completed transcription work without publishing an incomplete formal revision."""
import hashlib,json,math,wave
from pathlib import Path
from .db import encode,uid
from .job_checkpoints import JobCheckpoints,file_digest,validate_segments
from .alignment import aligned_or_chunk
from .live_results import RATE,plan_ranges
from .providers import get_asr_provider,checkpoint_identity_matches,merge_overlapping_chunks


def collect_progress(store,job):
    config=json.loads(job['config']);folder=store.directory(job['meeting_id'])/job['id']
    provider=get_asr_provider(config.get('engine','whisper'))
    if config.get('engine_identity') and not checkpoint_identity_matches(provider.engine_id,config['engine_identity'],provider.identity()):return None
    try:
        with wave.open(str(folder/'audio.wav')) as wav:duration=wav.getnframes()/wav.getframerate()
    except (OSError,wave.Error):return None
    c3=None;source_identity=None
    if (folder/'recovery.json').exists():
        c3=JobCheckpoints(store,job);source_identity={'sha256':file_digest(store.directory(job['meeting_id'])/'original')}
        if not c3.file_valid('audio',folder/'audio.wav',source_identity):return None
    chunks=[];covered=[]
    if config.get('completion_mode')=='quick':
        reused,gaps=plan_ranges(config.get('live_snapshot',[]),duration)
        for item in reused:
            start=item['start_frame']/RATE;end=item['end_frame']/RATE
            chunks.append([dict(s,id=uid(),speaker='未分配',start=max(start,s['start']),end=min(end,s['end'])) for s in item['segments'] if s['start']<end and s['end']>start]);covered.append((start,end))
        windows=[]
        for start,end in gaps:
            while start<end-1/RATE/2:
                stop=min(end,start+15);windows.append((start,stop));start=stop
        prefix='fast'
    else:
        seconds=config.get('chunk_seconds',provider.chunk_seconds)
        windows=[(max(0,i*seconds-2),min(duration,(i+1)*seconds+2)) for i in range(math.ceil(duration/seconds))];prefix='chunk'
    for index,(start,end) in enumerate(windows):
        checkpoint=folder/f'{prefix}-{index}.checkpoint.json'
        if c3:
            part=c3.load(f'{prefix}-{index}',[source_identity,provider.identity(),start,end],lambda value:validate_segments(value,duration))
            if part is None:continue
        elif checkpoint.exists():
            part=json.loads(checkpoint.read_text())
        else:
            # The old Qwen process writes decoded text before alignment validation.
            raw_path=folder/f'{prefix}-{index}.qwen.raw.json'
            if provider.engine_id!='qwen' or not raw_path.exists():continue
            part=recover_raw_alignment(raw_path,provider.model,end-start)
            if part is None:continue
            part=[dict(s,id=uid(),start=start+s['start'],end=min(end,start+s['end'])) for s in part]
        chunks.append(part);covered.append((start,end))
    if not chunks:return None
    # Union covered audio intervals; do not interpret overlapping chunk context as holes.
    cursor=0;missing=[]
    for start,end in sorted(covered):
        if start>cursor+1/RATE:missing.append({'start':cursor,'end':start})
        cursor=max(cursor,end)
    if cursor<duration-1/RATE:missing.append({'start':cursor,'end':duration})
    return {'segments':merge_overlapping_chunks(chunks),'missing_ranges':missing,'duration':duration,'completed_ranges':len(covered)}


def preserve_partial(store,job):
    result=collect_progress(store,job)
    if not result or not result['segments']:return None
    aid=hashlib.sha256(('transcription-progress:'+job['id']).encode()).hexdigest()[:32]
    provenance={'job':job['id'],'engine':json.loads(job['config']).get('engine','whisper'),'incomplete':True,'completion_mode':json.loads(job['config']).get('completion_mode','full'),
                'missing_ranges':result['missing_ranges'],'duration':result['duration'],'completed_ranges':result['completed_ranges']}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        meeting=db.execute('SELECT revision FROM meetings WHERE id=?',(job['meeting_id'],)).fetchone()
        if not meeting:return None
        db.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,'transcript_candidate',?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,provenance=excluded.provenance",(aid,job['meeting_id'],meeting['revision'],encode(result['segments']),encode(provenance)))
    return aid


def recover_raw_alignment(raw_path,model,duration):
    if not raw_path.exists():return None
    try:
        raw=json.loads(raw_path.read_text());attempts=raw.get('asr_attempts',[])
        if 'alignment' not in raw or not attempts or attempts[-1].get('capped',True) or raw.get('model')!=Path(model).name or abs(raw.get('duration',-1)-duration)>1/RATE:return None
        segments,warning=aligned_or_chunk(raw.get('text',''),raw['alignment'],duration)
        return segments
    except (ValueError,KeyError,TypeError):return None
