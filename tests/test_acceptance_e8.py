"""Hardware acceptance is user attestation plus scoped evidence, never a timer pass."""
import json,sqlite3,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.recording_acceptance import RecordingAcceptance,AcceptanceReview,evaluate
from tests.test_recording_validation import fixture


def finished(root):
    checks,native,recordings,runtime,process,rid,folder,clock=fixture(root)
    checks.environment=lambda:'stable-environment'
    acceptance=RecordingAcceptance(checks,lambda:'stable-environment')
    run=checks.start(rid,60)
    for n in range(12):
        clock[0]+=5
        with (folder/'system.pcm').open('ab') as handle:handle.write(b'\1\0'*16000)
        checks.sample()
    return checks,acceptance,run['id']


def review(assessment,**kwargs):
    return AcceptanceReview(case='short_capture',evidence_fingerprint=assessment['evidence_fingerprint'],verdict='passed',real_device=True,audio_reviewed=True,**kwargs)


def test_timer_and_synthetic_data_cannot_auto_pass_hardware(tmp_path):
    checks,a,cid=finished(tmp_path);result=a.assessment(cid)
    assert next(c for c in result['cases'] if c['case']=='short_capture')['state']=='needs_review'
    assert result['automatic_hardware_certification'] is False
    body=review(result).model_copy(update={'real_device':False})
    with pytest.raises(ValueError,match='真实设备'):a.review(cid,body)
    body=review(result).model_copy(update={'audio_reviewed':False})
    with pytest.raises(ValueError,match='回听'):a.review(cid,body)
    assert all(c['state']=='pending' for c in a.inventory())


def test_review_is_bound_to_report_and_runtime_and_survives_reopen(tmp_path):
    checks,a,cid=finished(tmp_path);before=a.assessment(cid)
    result=a.review(cid,review(before))
    assert result['cases'][0]['state']=='passed'
    restored=RecordingAcceptance(checks,lambda:'stable-environment')
    assert restored.assessment(cid)['cases'][0]['state']=='passed'
    changed=RecordingAcceptance(checks,lambda:'new-environment')
    assert changed.assessment(cid)['cases'][0]['state']=='stale'
    with pytest.raises(ValueError,match='已变化'):changed.review(cid,review(before))
    with checks.connect() as db:
        summary=json.loads(db.execute('SELECT summary FROM checks WHERE id=?',(cid,)).fetchone()[0]);summary['flags']['storage_unconfirmed']=1
        db.execute('UPDATE checks SET summary=? WHERE id=?',(json.dumps(summary),cid))
    assert a.assessment(cid)['cases'][0]['state']=='stale'
    with pytest.raises(ValueError,match='完整步骤'):a.review(cid,review(a.assessment(cid)))


def test_long_sleep_contention_and_asr_each_need_their_own_evidence():
    report={'state':'completed','elapsed_seconds':10800,'samples':[{'elapsed_seconds':0,'saved_bytes':1},{'elapsed_seconds':10800,'saved_bytes':2}],'flags':{},'max_observation_gap_seconds':10800}
    assert not evaluate(report,'long_capture')['eligible']
    assert not evaluate(report,'wake_return')['eligible']
    assert not evaluate(report,'other_app_changed')['eligible']
    report['markers']=[{'kind':'other_app_changed','elapsed':3}]
    assert not evaluate(report,'other_app_changed')['eligible']
    report['samples']=[{'elapsed_seconds':0,'asr':'failed','covered_seconds':5},{'elapsed_seconds':6,'asr':'running','covered_seconds':5,'file_growth_bytes':1}]
    assert evaluate(report,'other_app_changed')['eligible']
    assert not evaluate(report,'asr_recovery')['eligible']
    report['samples'][-1]['covered_seconds']=8
    assert evaluate(report,'asr_recovery')['eligible']
    assert not evaluate(report,'clean_stop')['eligible']
    report.update(end_reason='recording_stopped');report['samples'][-1].update(stop_confirmed=True,saved_bytes=100)
    assert evaluate(report,'clean_stop')['eligible']


def test_active_observation_cannot_be_accepted_and_rejection_is_recorded(tmp_path):
    checks,_,_,_,_,rid,_,_=fixture(tmp_path);a=RecordingAcceptance(checks,lambda:'test');checks.environment=lambda:'test';cid=checks.start(rid,60)['id']
    with pytest.raises(ValueError):a.review(cid,review(a.assessment(cid)))
    checks.stop(cid);snapshot=a.assessment(cid)
    rejected=review(snapshot).model_copy(update={'verdict':'failed','audio_reviewed':False})
    assert a.review(cid,rejected)['cases'][0]['state']=='failed'


def test_http_new_routes_preserve_old_observation_report_and_validation(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        assert client.get('/api/runtime/recording-checks/missing/acceptance').status_code==404
        assert client.post('/api/runtime/recording-checks/missing/acceptance-review',json={}).status_code==422
        result=client.get('/api/runtime/release-readiness')
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
        value=result.json();assert not value['capture_started'] and not value['models_started'] and not value['permissions_requested']
        assert not value['external_release_ready'] and not value['hardware_review_complete']
        assert client.get('/api/native-recordings').json()==[]


def test_acceptance_database_unavailable_does_not_disable_service(tmp_path):
    (tmp_path/'recording-validation.sqlite3').mkdir()
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        assert client.get('/api/runtime/recording-checks/missing/acceptance').status_code==503
        result=client.get('/api/runtime/release-readiness').json()
        assert not result['hardware_evidence_available'] and not result['external_release_ready']
        assert client.get('/api/runtime/lifecycle').status_code==200


def test_review_does_not_export_audio_text_or_recording_identifiers(tmp_path):
    checks,a,cid=finished(tmp_path);value=a.assessment(cid)
    assert cid not in json.dumps(value) and checks.get(cid)['recording_id'] not in json.dumps(value)
    assert 'samples' not in value and 'path' not in json.dumps(value)


def test_real_smoke_requires_explicit_opt_in_before_any_device_or_directory(tmp_path):
    import sys
    target=tmp_path/'never-created'
    result=subprocess.run([sys.executable,'scripts/hardware_smoke.py','--output',str(target)],capture_output=True,text=True)
    assert result.returncode==2 and '--run-real-device' in result.stderr
    assert not target.exists()


def test_legacy_or_other_runtime_observation_cannot_be_promoted_to_current_pass(tmp_path):
    checks,a,cid=finished(tmp_path)
    with checks.connect() as db:
        summary=json.loads(db.execute('SELECT summary FROM checks WHERE id=?',(cid,)).fetchone()[0]);summary.pop('capture_environment')
        db.execute('UPDATE checks SET summary=? WHERE id=?',(json.dumps(summary),cid))
    result=a.assessment(cid)
    assert not result['cases'][0]['eligible']
    with pytest.raises(ValueError,match='完整步骤'):a.review(cid,review(result))


def test_latest_failed_review_is_not_hidden_by_an_older_pass(tmp_path):
    checks,a,cid=finished(tmp_path);a.review(cid,review(a.assessment(cid)))
    assert a.inventory()[0]['state']=='passed'
    checks.wall=lambda:2000;checks.monotonic=lambda:2000
    newer=checks.start(checks.get(cid)['recording_id'],60)['id'];checks.stop(newer)
    body=review(a.assessment(newer)).model_copy(update={'verdict':'failed','audio_reviewed':False})
    a.review(newer,body)
    assert a.inventory()[0]['state']=='failed'
