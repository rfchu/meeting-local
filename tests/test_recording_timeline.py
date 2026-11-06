"""Evidence grouping is bounded and must never invent causes, missing audio or recovery."""
import json,time
from unittest.mock import Mock
from fastapi.testclient import TestClient
from app.main import create_app
from app.recording_timeline import build_timeline,MAX_EVENTS


def sample(seq,elapsed,flags=None,**fields):
    return {'seq':seq,'elapsed_seconds':elapsed,'capture':'recording','storage':'writing','asr':'disabled',
            'saved_bytes':1000,'saved_seconds':1,'covered_seconds':0,'flags':flags or [],**fields}


def test_repeated_flags_merge_but_manual_operations_stay_user_reported():
    rows=[sample(1,0,['capture_unknown']),sample(2,5,['capture_unknown']),sample(3,10,['capture_unknown']),sample(4,15)]
    result=build_timeline(rows,[{'seq':1,'elapsed':8,'kind':'other_app_changed'}])
    assert result['root_cause_determined'] is False and result['total_event_count']==2
    issue=next(x for x in result['events'] if x['kind']=='observation')
    assert issue['start_elapsed_seconds']==0 and issue['end_elapsed_seconds']==10 and issue['sample_count']==3
    assert issue['end_evidence']=='not_seen_in_next_sample'
    assert next(x for x in result['events'] if x['kind']=='manual_marker')['start_elapsed_seconds']==8


def test_sampling_gap_separates_episodes_without_claiming_they_recovered():
    result=build_timeline([sample(1,0,['microphone_no_data']),sample(2,35,['microphone_no_data','observation_gap'])],[])
    assert len(result['events'])==3
    issues=[x for x in result['events'] if x['code']=='microphone_no_data']
    assert len(issues)==2 and issues[-1]['end_evidence']=='observation_gap'
    gaps=[x for x in result['events'] if x['code']=='sampling_discontinuity']
    assert len(gaps)==1 and gaps[0]['start_elapsed_seconds']==0 and gaps[0]['end_elapsed_seconds']==35
    assert not any(x['code']=='observation_gap' for x in result['events'])


def test_flat_asr_coverage_requires_continuous_thirty_seconds_and_growing_files():
    rows=[sample(i+1,i*5,asr='running',saved_bytes=1000+i*1000) for i in range(7)]
    rows.append(sample(8,35,asr='running',saved_bytes=8000,covered_seconds=5))
    result=build_timeline(rows,[])
    progress=[x for x in result['events'] if x['kind']=='progress']
    assert len(progress)==1
    assert progress[0]['start_elapsed_seconds']==0 and progress[0]['end_elapsed_seconds']==30
    assert progress[0]['sample_count']==7 and progress[0]['end_context']['covered_seconds']==0
    assert not result['root_cause_determined']
    assert not build_timeline(rows[:6],[])['events']
    assert not build_timeline([dict(r,saved_bytes=1000) for r in rows],[])['events']
    assert not build_timeline([dict(r,asr='disabled') for r in rows],[])['events']


def test_pause_and_sampling_gaps_do_not_bridge_an_asr_flat_interval():
    rows=[sample(i+1,i*5,asr='waiting',saved_bytes=1000+i*1000) for i in range(11)]
    rows[4]['capture']='paused'
    assert not any(e['kind']=='progress' for e in build_timeline(rows,[])['events'])
    invalid=[sample(i+1,i*5,asr='running',saved_bytes=1000+i*1000) for i in range(11)];invalid.insert(5,None)
    assert not any(e['kind']=='progress' for e in build_timeline(invalid,[])['events'])
    rows=[sample(1,0,asr='running'),sample(2,40,asr='running',saved_bytes=5000)]
    assert not any(e['kind']=='progress' for e in build_timeline(rows,[])['events'])


def test_truncated_source_does_not_invent_an_observed_start_or_clean_history():
    result=build_timeline([sample(101,500,['capture_unknown'])],[],source_truncated=True)
    assert result['source_samples_truncated'] and result['retained_from_elapsed_seconds']==500
    assert result['events'][0]['start_elapsed_seconds']==500
    assert result['events'][0]['end_evidence']=='observation_ended'


def test_malformed_metadata_and_unknown_codes_do_not_leak_text_or_fabricate_positions():
    rows=[sample(1,5,['capture_unknown','private-code',{}],capture='private-name',saved_seconds='private-text',native_counter_delta={'private-device':5}),
          sample(True,10),sample(2,float('nan')),sample(1,4)]
    markers=[{'seq':1,'elapsed':4,'kind':{}},{'seq':2,'elapsed':None,'kind':'device_changed'}]
    result=build_timeline(rows,markers)
    assert result['invalid_samples']==3 and result['invalid_markers']==2
    assert 'private-' not in json.dumps(result)
    assert result['events'][0]['start_context']['saved_seconds'] is None


def test_latest_event_retention_keeps_a_long_running_observation_without_unbounded_output():
    rows=[sample(i+1,i*5,['system_no_data']+(['capture_unknown'] if i%2==0 else [])) for i in range(2500)]
    result=build_timeline(rows,[{'seq':1,'elapsed':0,'kind':'background_return'}])
    assert result['events_truncated'] and len(result['events'])==MAX_EVENTS
    assert result['total_event_count']>MAX_EVENTS
    assert any(e['code']=='system_no_data' and e['sample_count']==2500 for e in result['events'])
    assert result['events'][0]['end_elapsed_seconds']>=result['events'][-1]['end_elapsed_seconds']


def test_timeline_http_pagination_and_report_are_read_only(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        runtime=client.app.state.recording_runtime;recordings=runtime.recordings;native=runtime.native
        session=recordings.create('audio/wav');rid=session['id'];session['source']='macos';folder=recordings.folder(rid)
        recordings.write(folder/'session.json',session)
        recordings.write(folder/'capture.json',{'state':'recording','duration':1,'system_frames':16000})
        audio=folder/'system.pcm';audio.write_bytes(b'\1\0'*16000)
        process=Mock();process.poll.return_value=None;native.processes[rid]=process;native.last_seen[rid]=time.monotonic()-600
        checks=client.app.state.recording_validation;run=checks.start(rid,60)
        checks.mark(run['id'],'device_changed');checks.mark(run['id'],'other_app_changed')
        before=checks.get(run['id']);saved=audio.read_bytes();lease=native.last_seen[rid]
        base='/api/runtime/recording-checks/'+run['id']
        first=client.get(base+'/timeline?offset=0&limit=1').json();second=client.get(base+'/timeline?offset=1&limit=1').json()
        assert first['retained_event_count']==2 and first['events'][0]['key']!=second['events'][0]['key']
        report=client.get(base+'/report').json()
        assert report['schema_version']==3 and not report['timeline']['root_cause_determined']
        assert rid not in json.dumps(report) and run['id'] not in json.dumps(report)
        assert client.get(base+'/timeline?offset=-1').status_code==422
        assert client.get(base+'/timeline?limit=0').status_code==422
        assert client.get('/api/runtime/recording-checks/missing/timeline').status_code==404
        assert checks.get(run['id'])['sample_count']==before['sample_count']
        assert audio.read_bytes()==saved and native.last_seen[rid]==lease
        process.stdin.write.assert_not_called();native.processes.clear()


def test_pause_and_resume_are_system_state_observations_not_manual_assertions():
    result=build_timeline([sample(1,0),sample(2,5,capture='paused'),sample(3,10,capture='recording')],[])
    events=[e for e in result['events'] if e['code']=='capture_pause_state_changed']
    assert len(events)==2 and all(e['kind']=='state_change' and e['sample_count']==2 for e in events)
    assert events[0]['end_context']['capture']=='recording'
