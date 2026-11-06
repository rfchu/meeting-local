"""Controlled clocks and temporary audio; never records devices or invokes ASR."""
import json,sqlite3,time
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.recording_validation import RecordingValidation
from tests.test_recording_faults import native_fixture


def fixture(root):
    store,recordings,journal,native,runtime,rid,folder=native_fixture(root)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    native.last_seen[rid]=time.monotonic()-600
    clock=[0.]
    checks=RecordingValidation(root,recordings,native,runtime,wall=lambda:1000+clock[0],monotonic=lambda:clock[0])
    return checks,native,recordings,runtime,process,rid,folder,clock


def test_opt_in_observation_does_not_renew_page_lease_or_control_recording(tmp_path):
    checks,native,_,_,process,rid,folder,clock=fixture(tmp_path)
    before=(folder/'system.pcm').read_bytes();lease=native.last_seen[rid]
    assert checks.list()==[]
    run=checks.start(rid,60)
    assert checks.start(rid,60)['id']==run['id']  # lost-response retry
    for _ in range(12):clock[0]+=5;checks.sample()
    report=checks.report(run['id'])
    assert report['state']=='completed' and report['target_elapsed']
    assert report['result']=='manual_review_required' and report['audio_content_validated'] is False
    assert report['sample_count']==13 and report['flags']=={}
    assert report['elapsed_seconds']==60
    assert before==(folder/'system.pcm').read_bytes() and native.last_seen[rid]==lease
    process.stdin.write.assert_not_called();process.terminate.assert_not_called()
    assert 'recording_id' not in report and rid not in json.dumps(report)


def test_three_hour_clock_jump_is_gap_evidence_not_three_hour_content_validation(tmp_path):
    checks,_,_,_,_,rid,_,clock=fixture(tmp_path)
    run=checks.start(rid)
    clock[0]=10800;checks.sample();report=checks.report(run['id'])
    assert report['target_elapsed'] and report['flags']['observation_gap']==1
    assert report['sample_count']==2 and report['max_observation_gap_seconds']==10800
    assert not report['audio_content_validated'] and not report['sleep_wake_validated']
    assert not report['other_app_contention_validated']


def test_stopping_observation_leaves_capture_and_user_markers_are_not_proof(tmp_path):
    checks,native,_,_,process,rid,_,clock=fixture(tmp_path)
    run=checks.start(rid);clock[0]=7
    checks.mark(run['id'],'other_app_changed');checks.mark(run['id'],'wake_return')
    checks.stop(run['id']);checks.stop(run['id']);checks.sample()
    assert native.is_running(rid)
    report=checks.report(run['id'])
    assert report['state']=='stopped' and report['markers'][0]['elapsed']==7
    assert report['markers_are_user_reported'] and report['other_app_contention_validated'] is False
    with pytest.raises(ValueError):checks.mark(run['id'],'device_changed')
    process.stdin.write.assert_not_called();process.terminate.assert_not_called()


def test_capture_exit_and_service_restart_do_not_resume_observation_or_audio(tmp_path):
    checks,native,recordings,runtime,process,rid,folder,clock=fixture(tmp_path)
    run=checks.start(rid);process.poll.return_value=23;clock[0]=5;checks.sample()
    assert checks.get(run['id'])['state']=='interrupted'
    assert (folder/'system.pcm').read_bytes()==b'\1\0'*16000
    process.poll.return_value=None
    second=checks.start(rid)
    restored=RecordingValidation(tmp_path,recordings,native,runtime)
    restored.recover()
    assert restored.get(second['id'])['end_reason']=='service_restart'
    assert restored.get(second['id'])['state']=='interrupted'
    assert not restored.starts
    process.stdin.write.assert_not_called()


def test_stale_sampler_is_not_reported_as_fresh_and_status_failure_is_redacted(tmp_path,monkeypatch):
    checks,_,_,runtime,_,rid,_,clock=fixture(tmp_path)
    run=checks.start(rid);clock[0]=30
    assert not checks.get(run['id'])['status_fresh']
    def fail(*_):raise OSError('private-transcript-or-location')
    monkeypatch.setattr(runtime,'read',fail);checks.sample()
    report=checks.report(run['id'])
    assert report['flags']['status_unavailable']==1 and report['last_sample']['capture']=='unknown'
    assert 'private-' not in json.dumps(report)


def test_observer_disk_failure_does_not_block_capture_cleanup(tmp_path,monkeypatch):
    checks,native,_,_,process,rid,_,_=fixture(tmp_path);checks.start(rid)
    def fail():raise sqlite3.OperationalError('private-path')
    monkeypatch.setattr(checks,'connect',fail)
    checks.close()
    assert checks.available is False and native.is_running(rid)
    process.stdin.write.assert_not_called()


def test_http_listing_is_passive_and_start_requires_owned_native_recording(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        assert client.get('/api/runtime/recording-checks').json()==[]
        rid=client.post('/api/recordings',json={}).json()['id']
        url='/api/runtime/recording-checks'
        assert client.post(url,json={'recording_id':rid}).status_code==409
        assert client.post(url,json={'recording_id':rid,'target_seconds':0}).status_code==422
        assert client.post(url+'/missing/markers',json={'kind':'invented'}).status_code==422
        assert client.get(url+'/missing/report').status_code==404
        assert client.get('/api/native-recordings').json()==[]


def test_marker_and_history_retention_are_bounded(tmp_path):
    checks,_,_,_,_,rid,_,clock=fixture(tmp_path)
    run=checks.start(rid)
    for _ in range(200):checks.mark(run['id'],'device_changed')
    with pytest.raises(ValueError):checks.mark(run['id'],'device_changed')
    checks.stop(run['id'])
    for _ in range(21):
        clock[0]+=1;new=checks.start(rid);checks.stop(new['id'])
    assert len(checks.list())==20
    with pytest.raises(LookupError):checks.get(run['id'])


def test_lag_evidence_does_not_export_raw_runtime_payload(tmp_path,monkeypatch):
    checks,_,_,runtime,_,rid,_,_=fixture(tmp_path)
    original=runtime.read
    def view(key):
        result=original(key);result['private_text']='private-transcript'
        result['capture']['timeline_seconds']=120
        result['asr'].update(state='running',covered_seconds=0)
        return result
    monkeypatch.setattr(runtime,'read',view)
    run=checks.start(rid);report=checks.report(run['id'])
    assert report['flags']['asr_lag_observed']==1
    assert 'private-' not in json.dumps(report)
    assert not report['hardware_capture_validated']


def test_http_observe_mark_stop_report_without_capturing_devices(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        runtime=client.app.state.recording_runtime;recordings=runtime.recordings;native=runtime.native
        session=recordings.create('audio/wav');rid=session['id'];session['source']='macos'
        folder=recordings.folder(rid);recordings.write(folder/'session.json',session)
        recordings.write(folder/'capture.json',{'state':'recording','duration':1,'system_frames':16000})
        (folder/'system.pcm').write_bytes(b'\1\0'*16000)
        process=Mock();process.poll.return_value=None;native.processes[rid]=process
        response=client.post('/api/runtime/recording-checks',json={'recording_id':rid,'target_seconds':60})
        assert response.status_code==201
        cid=response.json()['id'];base=f'/api/runtime/recording-checks/{cid}'
        assert client.post(base+'/markers',json={'kind':'device_changed'}).status_code==200
        assert client.get('/api/runtime/recording-checks').json()[0]['marker_count']==1
        assert client.post(base+'/stop').json()['state']=='stopped'
        report=client.get(base+'/report')
        assert report.status_code==200 and report.headers['cache-control']=='no-store'
        assert 'attachment;' in report.headers['content-disposition']
        assert rid not in report.text and cid not in report.text
        assert report.json()['hardware_capture_validated'] is False
        process.stdin.write.assert_not_called();native.processes.clear()


def test_unavailable_observation_database_does_not_disable_meeting_service(tmp_path):
    (tmp_path/'recording-validation.sqlite3').mkdir()
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        assert client.get('/api/runtime/recording-checks').status_code==503
        assert client.get('/api/runtime/lifecycle').json()['available']
        rid=client.post('/api/recordings',json={}).json()['id']
        assert client.put(f'/api/recordings/{rid}/chunks/0',files={'file':('part',b'preserved')}).status_code==200


def test_sample_history_is_bounded_and_export_discloses_truncation(tmp_path):
    checks,_,_,_,_,rid,_,clock=fixture(tmp_path);run=checks.start(rid);cid=run['id']
    with checks.connect() as db:
        db.executemany('INSERT INTO samples VALUES(?,?,?,?,?)',[(cid,i,1000,0,'{}') for i in range(2,9006)])
        row=db.execute('SELECT summary FROM checks WHERE id=?',(cid,)).fetchone()
        summary=json.loads(row[0]);summary['sample_count']=9005
        db.execute('UPDATE checks SET summary=? WHERE id=?',(json.dumps(summary),cid))
    clock[0]=5;checks.sample();report=checks.report(cid)
    assert report['sample_count']==9006 and report['retained_sample_count']==9000 and report['samples_truncated']


def test_known_capture_exit_stays_interrupted_even_when_metadata_is_unreadable(tmp_path,monkeypatch):
    checks,_,_,runtime,process,rid,_,clock=fixture(tmp_path);run=checks.start(rid)
    process.poll.return_value=9;clock[0]=5
    def fail(*_):raise ValueError('unreadable metadata')
    monkeypatch.setattr(runtime,'read',fail);checks.sample()
    report=checks.report(run['id'])
    assert report['state']=='interrupted' and report['end_reason']=='capture_not_running'
    assert report['flags']['status_unavailable']==1


def test_failed_sample_write_does_not_erase_the_observation_gap(tmp_path):
    checks,_,_,_,_,rid,_,clock=fixture(tmp_path);run=checks.start(rid)
    with checks.connect() as db:
        db.execute("CREATE TRIGGER fail_sample BEFORE INSERT ON samples BEGIN SELECT RAISE(FAIL,'injected'); END")
    clock[0]=5
    with pytest.raises(sqlite3.Error):checks.sample()
    assert checks.get(run['id'])['sample_count']==1
    with checks.connect() as db:db.execute('DROP TRIGGER fail_sample')
    clock[0]=35;checks.sample();report=checks.report(run['id'])
    assert report['sample_count']==2 and report['flags']['observation_gap']==1
    assert report['max_observation_gap_seconds']==35


@pytest.mark.parametrize('published',[False,True])
def test_confirmed_recording_stop_ends_observation_without_false_capture_failure(tmp_path,published):
    checks,native,recordings,_,process,rid,folder,clock=fixture(tmp_path)
    run=checks.start(rid);before=(folder/'system.pcm').read_bytes();lease=native.last_seen[rid]
    recordings.write(folder/'capture.json',{'state':'stopped','duration':1,'error':'','system_frames':16000})
    if published:
        session=recordings.read(rid);session['state']='completed';recordings.write(folder/'session.json',session)
    process.poll.return_value=0;clock[0]=5;checks.sample()
    report=checks.report(run['id'])
    assert report['state']=='stopped' and report['end_reason']=='recording_stopped'
    assert report['sample_count']==2 and report['elapsed_seconds']==5
    assert report['last_sample']['stop_confirmed'] is True
    assert 'capture_not_running' not in report['flags'] and 'capture_ended' not in report['flags']
    assert not report['target_elapsed'] and not report['audio_content_validated']
    assert checks.stop(run['id'])['end_reason']=='recording_stopped'
    clock[0]=10;checks.sample();assert checks.get(run['id'])['sample_count']==2
    assert before==(folder/'system.pcm').read_bytes() and native.last_seen[rid]==lease
    process.stdin.write.assert_not_called();process.terminate.assert_not_called()


@pytest.mark.parametrize('metadata,exit_code,published',[
    ({'state':'failed','error':'private raw failure'},0,True),
    ({'state':'recording'},0,True),
    ({'state':'stopped','error':'private raw failure'},0,False),
    ({'state':'stopped','error':''},9,False),
    ({'state':'stopped','error':''},-9,False),
    ({'state':'stopped','error':'','diagnostics':{'version':1,'failure_stage':'storage'}},0,False),
])
def test_publish_or_zero_exit_does_not_hide_unconfirmed_capture_end(tmp_path,metadata,exit_code,published):
    checks,_,recordings,_,process,rid,folder,clock=fixture(tmp_path);run=checks.start(rid)
    recordings.write(folder/'capture.json',{'duration':1,'system_frames':16000,**metadata})
    if published:
        session=recordings.read(rid);session['state']='completed';recordings.write(folder/'session.json',session)
    # The health projection reads the persisted exit evidence without controlling capture.
    if exit_code!=-9:recordings.write(folder/'capture-exit.json',{'exit_code':exit_code,'observed_at':1005})
    process.poll.return_value=exit_code;clock[0]=5;checks.sample();report=checks.report(run['id'])
    assert report['state']=='interrupted' and report['end_reason']=='capture_not_running'
    assert report['last_sample']['stop_confirmed'] is False
    assert report['flags']['capture_not_running']==1
    assert 'private raw' not in json.dumps(report)


def test_clean_stop_on_target_boundary_is_recording_end_not_target_success(tmp_path):
    checks,_,recordings,_,process,rid,folder,clock=fixture(tmp_path);run=checks.start(rid,60)
    recordings.write(folder/'capture.json',{'state':'stopped','duration':1,'system_frames':16000})
    process.poll.return_value=0;clock[0]=60;checks.sample()
    report=checks.report(run['id'])
    assert report['state']=='stopped' and report['end_reason']=='recording_stopped'
    assert not report['target_elapsed'] and report['flags']['observation_gap']==1


def test_clean_capture_end_keeps_previous_and_terminal_asr_failures(tmp_path,monkeypatch):
    checks,_,recordings,runtime,process,rid,folder,clock=fixture(tmp_path)
    original=runtime.read
    def view(key):
        result=original(key);result['asr']['state']='failed';return result
    monkeypatch.setattr(runtime,'read',view);run=checks.start(rid)
    recordings.write(folder/'capture.json',{'state':'stopped','duration':1,'system_frames':16000})
    process.poll.return_value=0;clock[0]=5;checks.sample();report=checks.report(run['id'])
    assert report['end_reason']=='recording_stopped' and report['flags']['asr_attention']==2
    assert not report['audio_content_validated']


def test_stopped_asr_after_confirmed_recording_end_is_not_a_new_asr_failure(tmp_path,monkeypatch):
    checks,_,recordings,runtime,process,rid,folder,clock=fixture(tmp_path)
    run=checks.start(rid);original=runtime.read
    def view(key):
        result=original(key);result['asr']['state']='stopped';return result
    monkeypatch.setattr(runtime,'read',view)
    recordings.write(folder/'capture.json',{'state':'stopped','duration':1,'system_frames':16000})
    process.poll.return_value=0;clock[0]=5;checks.sample();report=checks.report(run['id'])
    assert report['end_reason']=='recording_stopped' and 'asr_attention' not in report['flags']
