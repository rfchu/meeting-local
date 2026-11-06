"""E1 host contract is local/read-only, scoped to project+data and never transfers capture."""
import hashlib,json,os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.host_bridge import identity,path_fingerprint
from app.paths import RuntimePaths


def test_host_contract_matches_workspace_without_exposing_paths_or_credentials(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY','synthetic-secret')
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        store=c.app.state.store
        with store.connect() as db:before=db.execute('SELECT count(*) FROM artifacts').fetchone()[0]
        first=c.get('/api/runtime/host');second=c.get('/api/runtime/host')
        assert first.status_code==200 and first.json()==second.json()
        value=first.json()
        assert value['product']=='cn.seeknow.tingji' and value['protocol_version']==1
        assert value['pid']==os.getpid() and value['data_fingerprint']==hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()
        assert value['capture_ownership']=={'browser':'recording_page','native':'api_service','host':'none'}
        assert value['restart_capture_reattach'] is False and value['workspace_path']=='/'
        assert 'synthetic-secret' not in first.text and str(tmp_path) not in first.text and str(c.app.state.paths.repository) not in first.text
        with store.connect() as db:assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==before
        assert not store.all('SELECT * FROM jobs')


def test_project_and_data_affinity_are_independent(tmp_path):
    a=RuntimePaths(tmp_path/'a',tmp_path/'data',tmp_path/'backups')
    b=RuntimePaths(tmp_path/'b',tmp_path/'data',tmp_path/'backups')
    c=RuntimePaths(tmp_path/'a',tmp_path/'other',tmp_path/'backups')
    first,second,third=map(identity,(a,b,c))
    assert first['project_fingerprint']!=second['project_fingerprint'] and first['data_fingerprint']==second['data_fingerprint']
    assert first['project_fingerprint']==third['project_fingerprint'] and first['data_fingerprint']!=third['data_fingerprint']


def test_affinity_canonicalizes_symlinks_and_supports_unicode_spaces(tmp_path):
    target=tmp_path/'项目 data';target.mkdir();link=tmp_path/'alias';link.symlink_to(target)
    assert path_fingerprint(target)==path_fingerprint(link)
    assert path_fingerprint(target)==hashlib.sha256(str(target.resolve()).encode('utf-8')).hexdigest()


@pytest.mark.parametrize('mode',['foreground','detached','launchd'])
def test_service_mode_and_identity_are_explicit(tmp_path,monkeypatch,mode):
    monkeypatch.setenv('TINGJI_SERVICE_MODE',mode);monkeypatch.setenv('TINGJI_INSTANCE_ID','synthetic-instance')
    value=identity(RuntimePaths(tmp_path,tmp_path/'data',tmp_path/'backups'))
    assert value['service_mode']==mode and value['instance_id']=='synthetic-instance'


@pytest.mark.parametrize('headers',[{'host':'remote.example'},{'origin':'https://remote.example'}])
def test_host_bridge_keeps_local_only_boundary(tmp_path,headers):
    with TestClient(create_app(tmp_path,start_worker=False)) as c:
        assert c.get('/api/runtime/host',headers=headers).status_code==403


def test_host_build_does_not_install_start_service_or_claim_capture_permissions():
    script=Path('scripts/build_host.sh').read_text()
    assert 'Library/Caches/cn.seeknow.tingji/Tingji.app' in script and 'manage_service.py' not in script
    assert 'NSMicrophoneUsageDescription' not in script and 'NSScreenCaptureUsageDescription' not in script
    assert '/Applications/' not in script and 'launchctl' not in script
