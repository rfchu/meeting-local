"""Composition and external HTTP compatibility after the B3 modular split."""
import hashlib,json
from pathlib import Path
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from app.main import create_app


def test_http_contract_matches_pre_split_v22(tmp_path):
    app=create_app(tmp_path/'contract',start_worker=False)
    schema=app.openapi()
    assert schema['paths'].pop('/api/runtime/resources')
    assert schema['paths'].pop('/api/meetings/{mid}/minutes/jobs')
    assert schema['components']['schemas'].pop('MinutesTaskInput')
    payload=json.dumps(schema,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    # Captured from the running v22 service before splitting: 116 paths, 135 operations.
    assert hashlib.sha256(payload.encode()).hexdigest()=='fac4d2387540dd6ea2f8f29205390b870e6d7a8695f1f743831ce1f2ec026b90'
    operations=[(method,route.path) for route in app.routes if isinstance(route,APIRoute) for method in route.methods]
    assert len(operations)==len(set(operations))==137


def test_two_applications_do_not_share_recording_or_meeting_resources(tmp_path):
    left=create_app(tmp_path/'left',start_worker=False);right=create_app(tmp_path/'right',start_worker=False)
    for name in ('store','worker','recordings','live_sessions','live_asr_lock','recording_commands','resident','meetings','configurations'):
        assert getattr(left.state.services,name) is not getattr(right.state.services,name)
    assert left.state.worker is left.state.services.worker
    assert left.state.live_sessions is left.state.services.live_sessions
    mid='a'*32
    left.state.store.execute("INSERT INTO meetings(id,title,filename) VALUES(?,?,?)",(mid,'Synthetic isolation','fixture.wav'))
    with TestClient(left) as a,TestClient(right) as b:
        assert a.get('/api/meetings/'+mid).status_code==200
        assert b.get('/api/meetings/'+mid).status_code==404
        recording=a.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        assert b.get('/api/recordings/'+recording+'/health').status_code==404


@pytest.mark.parametrize('path',['/api/runtime/overview','/api/retrieval','/api/recordings','/api/meetings','/api/minutes-templates'])
def test_local_only_guard_covers_every_feature_router(tmp_path,path):
    with TestClient(create_app(tmp_path/'security',start_worker=False)) as client:
        assert client.get(path,headers={'host':'remote.example'}).status_code==403
        assert client.get(path,headers={'origin':'https://remote.example'}).status_code==403


def test_explicit_external_workspace_paths_are_shared_by_app_and_maintenance(tmp_path,monkeypatch):
    import os,subprocess,sys
    from app.paths import RuntimePaths
    source=tmp_path/'local-data';destination=tmp_path/'local-backups'
    monkeypatch.setenv('MEETING_DATA',str(source));monkeypatch.setenv('MEETING_BACKUPS',str(destination))
    paths=RuntimePaths.from_env()
    assert paths.data==source and paths.backups==destination
    assert not source.exists() and not destination.exists()
    app=create_app(start_worker=False)
    assert app.state.store.root==app.state.paths.data==source
    run=subprocess.run([sys.executable,'-m','scripts.manage_backups','create'],capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert Path(result['backup_directory']).parent==destination and result['status']=='verified'
    assert create_app(tmp_path/'override',start_worker=False).state.store.root!=source


def test_restored_external_directory_can_start_without_switching_original(tmp_path):
    from app.backups import create_backup,restore_backup
    original=create_app(tmp_path/'original',start_worker=False)
    original.state.store.execute("INSERT INTO meetings(id,title,filename) VALUES(?,?,?)",('b'*32,'Synthetic restored workspace','fixture.wav'))
    bundle=tmp_path/'backup';create_backup(original.state.store.root,bundle)
    restored=tmp_path/'external'/'restored';restore_backup(bundle,restored)
    with TestClient(create_app(restored,start_worker=False)) as client:
        assert client.get('/api/meetings').json()[0]['id']=='b'*32
    assert original.state.store.one('SELECT title FROM meetings')['title']=='Synthetic restored workspace'
