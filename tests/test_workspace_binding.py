"""E3 optional native cookie binds every request before route side effects."""
import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.host_bridge import workspace_binding

@pytest.fixture
def pair(tmp_path):
    left=create_app(tmp_path/'left',start_worker=False);right=create_app(tmp_path/'right',start_worker=False)
    with TestClient(left) as a,TestClient(right) as b:yield left,right,a,b

def test_legacy_browser_contract_is_unchanged(pair):
    _,_,a,_=pair
    assert a.get('/api/runtime/host').status_code==200
    assert 'permissions-policy' not in a.get('/').headers


def test_matching_binding_and_permission_policy(pair):
    left,_,a,_=pair;a.cookies.set('tingji_workspace_binding',workspace_binding(left.state.paths))
    assert a.get('/api/meetings').status_code==200
    assert 'microphone=()' in a.get('/').headers['permissions-policy']

@pytest.mark.parametrize('path',['/','/api/meetings','/api/runtime/host','/assets/missing.js'])
def test_mismatched_binding_blocks_all_routes(pair,path):
    left,_,_,b=pair;b.cookies.set('tingji_workspace_binding',workspace_binding(left.state.paths))
    response=b.get(path);assert response.status_code==409 and response.json()['detail']['code']=='workspace_mismatch'


def test_wrong_workspace_write_is_rejected_before_mutation(pair):
    left,right,_,b=pair;b.cookies.set('tingji_workspace_binding',workspace_binding(left.state.paths))
    assert b.put('/api/meeting-preparation',json={'title':'must not write'}).status_code==409
    assert not right.state.store.one('SELECT * FROM meeting_preparation')


def test_empty_or_forged_cookie_cannot_fall_back_to_legacy(pair):
    _,_,a,_=pair
    for value in ('','forged','0'*64):
        a.cookies.set('tingji_workspace_binding',value)
        assert a.get('/api/meetings').status_code==409


def test_binding_changes_with_project_and_data_not_backup_path(tmp_path):
    from app.paths import RuntimePaths
    a=RuntimePaths(tmp_path/'repo',tmp_path/'data',tmp_path/'backup')
    assert workspace_binding(a)!=workspace_binding(RuntimePaths(a.repository,tmp_path/'other',a.backups))
    assert workspace_binding(a)!=workspace_binding(RuntimePaths(tmp_path/'other',a.data,a.backups))
    assert workspace_binding(a)==workspace_binding(RuntimePaths(a.repository,a.data,tmp_path/'elsewhere'))
