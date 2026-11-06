"""Synthetic input and elapsed timelines; these tests do not access real devices."""
import time
from unittest.mock import Mock
from tests.test_recording_faults import native_fixture


def test_track_loss_recovery_is_independent_of_capture_and_asr(tmp_path):
    _,recordings,journal,native,runtime,rid,folder=native_fixture(tmp_path)
    session=recordings.read(rid);session['include_microphone']=True
    recordings.write(folder/'session.json',session)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    for missing in (False,True,True,False):
        recordings.write(folder/'capture.json',{'state':'recording','duration':60,
            'system_frames':960000,'microphone_frames':32000,
            'buffer_age':{'system':0,'microphone':20 if missing else 0}})
        runtime.observe();view=runtime.read(rid)
        assert view['phase']=='recording' and view['asr']['state']=='disabled'
        assert view['capture']['tracks'][1]['state']==('no_data' if missing else 'receiving')
    codes=[e['code'] for e in journal.events(rid)]
    assert codes.count('microphone_input.no_data')==1
    assert codes.count('microphone_input.receiving')==2
    process.terminate.assert_not_called()
    assert (folder/'system.pcm').stat().st_size==32000


def test_three_hour_background_clock_does_not_stop_capture_or_repeat_events(tmp_path):
    _,_,journal,native,runtime,rid,_=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process
    native.last_seen[rid]=time.monotonic()-10800
    for _ in range(30):native.stop_abandoned();native.status(rid,touch=False)
    assert [e['code'] for e in journal.events(rid)].count('page.detached')==1
    assert rid in native.detached
    native.status(rid);native.stop_abandoned()
    assert rid not in native.detached
    assert [e['code'] for e in journal.events(rid)].count('page.returned')==1
    process.stdin.write.assert_not_called();process.terminate.assert_not_called()


def test_three_hour_pcm_timeline_remains_readable_after_background_and_input_loss():
    from scripts.recording_endurance import run
    report=run(10800,speed=0)
    assert report['passed'] and report['input_loss_observed']
    assert report['checks']==720 and report['saved_bytes']==691200000
    assert report['hardware_capture_validated'] is False


def test_record_only_capture_can_be_reattached_without_starting_recognition(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        app=client.app;recordings=app.state.recording_runtime.recordings;native=app.state.recording_runtime.native
        session=recordings.create('audio/wav');rid=session['id'];folder=recordings.folder(rid)
        session.update(source='macos');recordings.write(folder/'session.json',session)
        recordings.write(folder/'capture.json',{'state':'recording','duration':10800})
        from scripts.recording_endurance import SyntheticProcess
        native.processes[rid]=SyntheticProcess();native.last_seen[rid]=time.monotonic()-600
        native.stop_abandoned()
        rows=client.get('/api/native-recordings').json()
        assert rows[0]['recording']['id']==rid and rows[0]['recording']['duration']==10800
        assert rows[0]['config'] is None and rid in native.detached
        assert client.get('/api/live-sessions').json()==[]
        # Explicit status viewing reattaches; listing alone does not claim page ownership.
        assert client.get(f'/api/native-capture/{rid}').status_code==200
        assert rid not in native.detached
        native.processes.clear()
