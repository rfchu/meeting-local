"""Numeric/enum allowlist for native telemetry; never device names, IDs or raw errors."""
COUNTERS=('default_input_changes','default_output_changes','audio_device_connections','audio_device_disconnections','listener_failures','device_query_failures')
TRACK_COUNTERS=('input_buffers','empty_outputs','dropped_buffers','format_changes','gap_frames')
STAGES={'none','startup','capture_stream','audio_conversion','storage','timeline_limit'}


def count(value):return value if type(value) is int and 0<=value<=2**63-1 else None


def sanitize(raw):
    if not isinstance(raw,dict) or type(raw.get('version')) is not int or raw['version']!=1:return {'available':False}
    result={'version':1,'available':True,'helper_build':count(raw.get('helper_build')),**{key:count(raw.get(key)) for key in COUNTERS}}
    result['configured_microphone_is_default']=raw.get('configured_microphone_is_default') if type(raw.get('configured_microphone_is_default')) is bool else None
    stage=raw.get('failure_stage');result['failure_stage']=stage if isinstance(stage,str) and stage in STAGES else 'unknown'
    error=raw.get('stream_error_code');result['stream_error_code']=error if type(error) is int and -(2**31)<=error<2**31 else None
    tracks=raw.get('tracks',{});tracks=tracks if isinstance(tracks,dict) else {}
    result['tracks']={}
    for name in ('system','microphone'):
        values=tracks.get(name,{});values=values if isinstance(values,dict) else {}
        result['tracks'][name]={key:count(values.get(key)) for key in TRACK_COUNTERS}
    result['counters_complete']=all(result.get(k) is not None for k in COUNTERS) and all(v is not None for values in result['tracks'].values() for v in values.values())
    return result


def flatten(raw):
    if not raw.get('available'):return {}
    return {**{key:raw.get(key) for key in COUNTERS},
            **{name+'.'+key:raw['tracks'][name].get(key) for name in ('system','microphone') for key in TRACK_COUNTERS}}


def observe(summary,diagnostics):
    current={key:value for key,value in flatten(diagnostics).items() if value is not None}
    flags=[];delta={}
    if diagnostics.get('listener_failures'):flags.append('native_device_monitor_limited')
    if diagnostics.get('configured_microphone_is_default') is False:flags.append('configured_mic_differs_from_default')
    if diagnostics.get('failure_stage') not in ('none',None,'unknown'):flags.append('native_failure_reported')
    if not current:return flags,delta
    previous=summary.get('native_last_counters')
    if previous is None:
        summary['native_counter_baseline']=dict(current)
        if diagnostics.get('device_query_failures'):flags.append('native_device_read_failed')
    else:
        for key,value in current.items():
            if key not in previous:continue
            if value<previous[key]:flags.append('native_counters_reset')
            elif value>previous[key]:delta[key]=value-previous[key]
    for key,value in current.items():summary.setdefault('native_counter_baseline',{}).setdefault(key,value)
    summary['native_last_counters']={**(previous or {}),**current}
    totals=summary.setdefault('native_counter_increases',{})
    for key,value in delta.items():totals[key]=totals.get(key,0)+value
    for key in delta:
        if key=='default_input_changes':flags.append('default_input_changed')
        elif key=='default_output_changes':flags.append('default_output_changed')
        elif key=='device_query_failures':flags.append('native_device_read_failed')
        elif key.startswith('audio_device_'):flags.append('audio_device_changed')
        elif key.endswith('.dropped_buffers'):flags.append('native_buffers_dropped')
        elif key.endswith('.format_changes'):flags.append('audio_format_changed')
        elif key.endswith('.gap_frames') and delta[key]>=1600:flags.append('audio_timeline_padding')
    return list(dict.fromkeys(flags)),delta
