"""Telemetry validation uses numeric fixtures, never actual audio devices."""
import json
from unittest.mock import Mock
import pytest
from app.capture_diagnostics import sanitize,observe,COUNTERS,TRACK_COUNTERS
from app.recording_validation import RecordingValidation
from tests.test_recording_faults import native_fixture


def telemetry():
    return {'version':1,'helper_build':2,**dict.fromkeys(COUNTERS,0),'configured_microphone_is_default':True,
            'failure_stage':'none','stream_error_code':None,
            'tracks':{name:dict.fromkeys(TRACK_COUNTERS,0) for name in ('system','microphone')}}


def test_allowlist_removes_device_id_names_raw_errors_and_nested_content():
    raw=telemetry();raw.update(device_id='private-uid',device_name='private-name',error='private-error')
    raw['tracks']['system']['text']='private-transcript'
    result=sanitize(raw)
    assert result['available'] and result['counters_complete'] and result['helper_build']==2
    assert 'private-' not in json.dumps(result)
    assert sanitize(result)==result


@pytest.mark.parametrize('raw',[None,[],{'version':True},{'version':2},{'version':'1'}])
def test_unknown_telemetry_is_unavailable_not_zero(raw):
    assert sanitize(raw)=={'available':False}


def test_bad_counters_are_unknown_and_error_code_is_numeric_only():
    raw=telemetry();raw.update(default_input_changes=True,default_output_changes=-1,stream_error_code='private-error',failure_stage='private-stage')
    raw['tracks']['system']['gap_frames']=float('inf')
    data=sanitize(raw)
    assert not data['counters_complete']
    assert data['default_input_changes'] is None and data['default_output_changes'] is None
    assert data['tracks']['system']['gap_frames'] is None and data['stream_error_code'] is None
    assert data['failure_stage']=='unknown' and 'private-' not in json.dumps(data)


def test_observation_uses_first_valid_counters_as_baseline_and_preserves_missing_values():
    summary={};first=telemetry();first['default_input_changes']=2
    flags,delta=observe(summary,sanitize(first));assert flags==[] and delta=={}
    missing=telemetry();missing['default_input_changes']=None
    observe(summary,sanitize(missing))
    changed=telemetry();changed['default_input_changes']=3
    changed['tracks']['system'].update(dropped_buffers=1,format_changes=1,gap_frames=3200)
    flags,delta=observe(summary,sanitize(changed))
    assert delta['default_input_changes']==1 and delta['system.gap_frames']==3200
    assert {'default_input_changed','native_buffers_dropped','audio_format_changed','audio_timeline_padding'}<=set(flags)
    assert summary['native_counter_baseline']['default_input_changes']==2
    assert summary['native_counter_increases']['default_input_changes']==1
    flags,delta=observe(summary,sanitize(telemetry()))
    assert 'native_counters_reset' in flags and all(v>0 for v in delta.values())


def test_small_padding_does_not_raise_missing_voice_claim_or_padding_alert():
    summary={};observe(summary,sanitize(telemetry()))
    raw=telemetry();raw['tracks']['system']['gap_frames']=20
    flags,delta=observe(summary,sanitize(raw))
    assert delta['system.gap_frames']==20 and 'audio_timeline_padding' not in flags


def test_structured_storage_stage_is_used_without_localized_error_prefix(tmp_path):
    _,recordings,_,native,runtime,rid,folder=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    raw=telemetry();raw['failure_stage']='storage'
    recordings.write(folder/'capture.json',{'state':'failed','duration':1,'error':'different locale','diagnostics':raw})
    assert runtime.read(rid)['storage']['state']=='failed'
    assert runtime.health.snapshot(rid)['capture_failure_code']=='storage_failed'


def test_health_and_observation_export_correlate_file_growth_and_telemetry_without_private_data(tmp_path):
    _,recordings,_,native,runtime,rid,folder=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    raw=telemetry();raw['default_input_changes']=2;raw['device_name']='private-device'
    def write():recordings.write(folder/'capture.json',{'state':'recording','duration':6,'system_frames':96000,'buffer_age':{'system':0},'diagnostics':raw})
    write();clock=[0.]
    checks=RecordingValidation(tmp_path,recordings,native,runtime,wall=lambda:1000+clock[0],monotonic=lambda:clock[0])
    run=checks.start(rid,60)
    raw['default_input_changes']=3;raw['tracks']['system']['dropped_buffers']=1
    (folder/'system.pcm').write_bytes(b'\1\0'*96000);clock[0]=5;write();checks.sample()
    checks.stop(run['id']);report=checks.report(run['id'])
    assert report['schema_version']==2 and report['native_diagnostics_available']
    assert report['native_counter_baseline']['default_input_changes']==2
    assert report['native_counter_increases']['default_input_changes']==1
    assert report['last_sample']['file_growth_bytes']==160000
    assert report['flags']['default_input_changed']==1
    assert 'private-' not in json.dumps(report) and rid not in json.dumps(report)
    assert 'private-' not in json.dumps(runtime.health.snapshot(rid))
    process.stdin.write.assert_not_called();process.terminate.assert_not_called()


def test_old_helpers_remain_usable_but_internal_diagnostics_are_not_fabricated(tmp_path):
    _,recordings,_,native,runtime,rid,_=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    checks=RecordingValidation(tmp_path,recordings,native,runtime)
    report=checks.report(checks.start(rid,60)['id'])
    assert report['last_sample']['native_diagnostics']=={'available':False}
    assert not report['native_diagnostics_available']
    assert report['flags']=={}
    assert 'native_counter_baseline' not in report


def test_converter_buffering_and_device_query_failures_are_not_reported_as_dropped_audio_or_device_changes():
    summary={};observe(summary,sanitize(telemetry()))
    raw=telemetry();raw['device_query_failures']=1;raw['tracks']['system']['empty_outputs']=3
    flags,delta=observe(summary,sanitize(raw))
    assert 'native_device_read_failed' in flags
    assert 'default_input_changed' not in flags and 'native_buffers_dropped' not in flags
    assert delta['system.empty_outputs']==3
