"""Read-only evidence timeline from bounded metadata. No causal diagnoses or content."""
import math,heapq
from .capture_diagnostics import COUNTERS,TRACK_COUNTERS

FLAGS={'observation_gap','capture_not_running','capture_ended','capture_unknown','storage_unconfirmed',
       'asr_attention','asr_lag_observed','system_no_data','microphone_no_data','status_unavailable',
       'default_input_changed','default_output_changed','audio_device_changed','native_buffers_dropped',
       'audio_format_changed','audio_timeline_padding','configured_mic_differs_from_default',
       'native_device_monitor_limited','native_device_read_failed','native_failure_reported','native_counters_reset','audio_file_shrank'}
MARKERS={'background_return','device_changed','other_app_changed','pause_resume','wake_return'}
STATES={'starting','recording','paused','unknown','interrupted','stopped','saving','saved','waiting','writing','available','partial','missing','failed','running','behind','disabled','unconfirmed'}
DELTA_KEYS=set(COUNTERS)|{name+'.'+key for name in ('system','microphone') for key in TRACK_COUNTERS}
MAX_EVENTS=1000


def number(value,*,signed=False):
    if type(value) not in (int,float) or abs(value)>2**63-1 or not math.isfinite(value) or (value<0 and not signed):return None
    return value


def context(row):
    result={key:row.get(key) if row.get(key) in STATES else 'unknown' for key in ('capture','storage','asr') if isinstance(row.get(key),str)}
    for key in ('saved_seconds','covered_seconds','saved_bytes'):
        result[key]=number(row.get(key))
    result['file_growth_bytes']=number(row.get('file_growth_bytes'),signed=True)
    delta=row.get('native_counter_delta',{})
    result['native_counter_delta']={key:value for key,value in delta.items() if key in DELTA_KEYS and number(value) is not None} if isinstance(delta,dict) else {}
    return result


def build_timeline(samples,markers,*,source_truncated=False):
    events=[];active={};previous=None;flat=None;invalid=0;valid=0;first=None;last=None;total=0;unrecognized=0;last_seq=0
    def add(item):
        nonlocal total
        total+=1
        entry=(item['end_elapsed_seconds'],item['start_elapsed_seconds'],item['key'],total,item)
        if len(events)<MAX_EVENTS:heapq.heappush(events,entry)
        else:heapq.heappushpop(events,entry)
    def event(kind,code,row):
        elapsed=row['elapsed_seconds']
        return {'key':f'{kind}:{code}:{row["seq"]}','kind':kind,'code':code,
                'start_elapsed_seconds':elapsed,'end_elapsed_seconds':elapsed,
                'sample_count':0 if kind=='manual_marker' else 1,
                'start_context':context(row),'end_context':context(row),'end_evidence':'observation_ended'}
    def finish_flat(reason):
        nonlocal flat
        if flat:
            duration=flat['end_elapsed_seconds']-flat['start_elapsed_seconds']
            first_bytes=flat['start_context']['saved_bytes'];last_bytes=flat['end_context']['saved_bytes']
            if duration>=30 and first_bytes is not None and last_bytes is not None and last_bytes>first_bytes:
                flat['end_evidence']=reason;add(flat)
        flat=None
    for raw in samples:
        if not isinstance(raw,dict):
            invalid+=1
            for item in active.values():item['end_evidence']='observation_gap';add(item)
            active={};finish_flat('observation_gap');previous=None
            continue
        elapsed=number(raw.get('elapsed_seconds'));seq=raw.get('seq')
        if elapsed is None or type(seq) is not int or not 1<=seq<=2**63-1 or seq<=last_seq or (last is not None and elapsed<last):
            invalid+=1
            for item in active.values():item['end_evidence']='observation_gap';add(item)
            active={};finish_flat('observation_gap');previous=None
            continue
        row=dict(raw,elapsed_seconds=elapsed);valid+=1
        if first is None:first=elapsed
        last=elapsed;last_seq=seq
        continuous=bool(previous and seq==previous['seq']+1 and elapsed-previous['elapsed_seconds']<=20)
        gap=bool(previous and not continuous)
        if gap:
            for item in active.values():item['end_evidence']='observation_gap';add(item)
            active={};finish_flat('observation_gap')
            item=event('observation','sampling_discontinuity',previous)
            item['key']=f'gap:{previous["seq"]}:{seq}';item['end_elapsed_seconds']=elapsed
            item['end_context']=context(row);item['sample_count']=0;item['end_evidence']='next_sample_observed'
            add(item)
        if continuous and previous.get('capture') in ('recording','paused') and row.get('capture') in ('recording','paused') and previous['capture']!=row['capture']:
            item=event('state_change','capture_pause_state_changed',previous)
            item['key']=f'pause:{previous["seq"]}:{seq}';item['end_elapsed_seconds']=elapsed;item['end_context']=context(row);item['sample_count']=2;item['end_evidence']='state_change_observed';add(item)
        raw_flags=row.get('flags',[])
        unrecognized+=sum(1 for flag in raw_flags if not isinstance(flag,str) or flag not in FLAGS) if isinstance(raw_flags,list) else 1
        flags={f for f in raw_flags if isinstance(f,str) and f in FLAGS} if isinstance(raw_flags,list) else set()
        if gap:flags.discard('observation_gap')  # The interval entry already describes this gap.
        for code in list(active):
            if code not in flags:
                item=active.pop(code);item['end_evidence']='not_seen_in_next_sample';add(item)
        for code in sorted(flags):
            if code in active and continuous:
                item=active[code];item['end_elapsed_seconds']=elapsed;item['end_context']=context(row);item['sample_count']+=1
            else:
                item=event('observation',code,row);active[code]=item
        data=context(row)
        eligible=(data.get('capture')=='recording' and data.get('asr') in ('running','waiting','behind')
                  and data['covered_seconds'] is not None and data['saved_bytes'] is not None)
        same=(flat and continuous and eligible and data['covered_seconds']==flat['start_context']['covered_seconds']
              and data['saved_bytes']>=flat['end_context']['saved_bytes'])
        if same:
            flat['end_elapsed_seconds']=elapsed;flat['end_context']=data;flat['sample_count']+=1
        else:
            finish_flat('coverage_or_observation_changed')
            if eligible:flat=event('progress','asr_coverage_flat_with_file_growth',row)
        previous=row
    finish_flat('observation_ended')
    for item in active.values():add(item)
    invalid_markers=0
    for marker in markers:
        if not isinstance(marker,dict):invalid_markers+=1;continue
        elapsed=number(marker.get('elapsed'));kind=marker.get('kind');seq=marker.get('seq')
        if elapsed is None or not isinstance(kind,str) or kind not in MARKERS or type(seq) is not int or not 1<=seq<=2**63-1:invalid_markers+=1;continue
        add(event('manual_marker',kind,{'seq':seq,'elapsed_seconds':elapsed}))
    kept=[entry[4] for entry in sorted(events,reverse=True)]
    return {'version':1,'root_cause_determined':False,'order':'latest_observation_first',
            'unrecognized_flag_count':unrecognized,'source_samples_truncated':bool(source_truncated),'invalid_samples':invalid,'invalid_markers':invalid_markers,
            'valid_sample_count':valid,'retained_from_elapsed_seconds':first,'retained_to_elapsed_seconds':last,
            'total_event_count':total,'retained_event_count':len(kept),'events_truncated':total>len(kept),'events':kept}
