"""B1 uses temporary databases/audio and fresh restore roots, never starts models."""
import hashlib,json,os,sqlite3,stat,subprocess,sys,time,uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.db import Store
from app.recordings import RecordingStore
from app.lifecycle import Lifecycle
from app.maintenance import DataLease
from app.main import create_app
from app.backups import BackupError,create_backup,verify_backup,restore_backup,drill_backup
import app.backups as backups


def sample(root):
    store=Store(root);mid=uuid.uuid4().hex;folder=store.directory(mid);folder.mkdir()
    audio=b'synthetic-original';(folder/'original').write_bytes(audio)
    (folder/'asset.json').write_text(json.dumps({'size':len(audio),'sha256':hashlib.sha256(audio).hexdigest()}))
    (folder/'minutes.docx').write_bytes(b'synthetic-derived-document')
    store.execute('INSERT INTO meetings(id,title,filename,segments) VALUES(?,?,?,?)',(mid,'PRIVATE-TITLE','meeting.wav','[{"text":"PRIVATE-TRANSCRIPT"}]'))
    store.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload) VALUES(?,?,0,'meeting_analysis',?)",(uuid.uuid4().hex,mid,'{"private":"PRIVATE-CONTENT"}'))
    store.execute("INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES('profile','local','local','http://localhost:1234/v1','model','PRIVATE-KEY')")
    rec=RecordingStore(store);session=rec.create('audio/wav');session.update(source='macos',state='interrupted',control={'version':1,'pending':{'command':'pause'},'receipts':[]})
    path=rec.folder(session['id']);rec.write(path/'session.json',session);rec.write(path/'capture.json',{'state':'interrupted','duration':1})
    (path/'system.pcm').write_bytes(b'\1\0'*16000)
    journal=Lifecycle(root);journal.record('capture.interrupted',session['id'])
    (root/'services').mkdir();(root/'services'/'detached.json').write_text('{"pid":999999}')
    (root/'meeting.db').touch()  # Legacy empty file must be preserved, not called a valid DB.
    return store,mid,session['id']


def test_backup_restore_drill_preserve_originals_metadata_secrets_and_derived_files(tmp_path):
    source=tmp_path/'source';store,mid,rid=sample(source)
    package=tmp_path/'package';result=create_backup(source,package)
    assert result['database_count']==2 and result['logical_inventory']['meeting_count']==1
    assert result['logical_inventory']['artifact_count']==1 and result['logical_inventory']['unclosed_recording_count']==1
    assert not result['audio_content_validated'] and result['logical_inventory']['missing_original_count']==0
    manifest=json.loads((package/'manifest.json').read_text())
    serialized=json.dumps(manifest)
    for secret in ('PRIVATE-TITLE','PRIVATE-TRANSCRIPT','PRIVATE-CONTENT','PRIVATE-KEY'):assert secret not in serialized
    assert not (package/'data'/'services').exists() and (package/'data'/'meeting.db').stat().st_size==0
    target=tmp_path/'restored';report=restore_backup(package,target)
    assert report['status']=='restore_verified' and not report['workers_started'] and not report['source_overwritten']
    assert (target/mid/'original').read_bytes()==(source/mid/'original').read_bytes()
    assert (target/mid/'minutes.docx').read_bytes()==b'synthetic-derived-document'
    assert json.loads((target/'recordings'/rid/'session.json').read_text())['control']['pending']['command']=='pause'
    with sqlite3.connect(target/'meetings.sqlite3') as db:assert db.execute('SELECT api_key FROM llm_configs').fetchone()[0]=='PRIVATE-KEY'
    assert not (target/'services').exists() and not (target/'.backup-payload').exists()
    assert stat.S_IMODE(package.stat().st_mode)==0o700
    assert all(stat.S_IMODE((package/'data'/e['path']).stat().st_mode)==0o600 for e in manifest['files'])
    assert drill_backup(package)['temporary_restore_removed'] and verify_backup(package)['status']=='verified'


def test_wal_commits_are_included_and_every_database_is_checked(tmp_path):
    source=tmp_path/'source';store,_,_=sample(source)
    connection=sqlite3.connect(source/'meetings.sqlite3')
    try:
        connection.execute('PRAGMA journal_mode=WAL');connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute("INSERT INTO meetings(id,title,filename) VALUES(?,?,?)",(uuid.uuid4().hex,'wal-only','wal.wav'));connection.commit()
        assert (source/'meetings.sqlite3-wal').stat().st_size>0
        result=create_backup(source,tmp_path/'backup')
        assert result['logical_inventory']['meeting_count']==2 and result['logical_inventory']['missing_original_count']==1
        assert not (tmp_path/'backup/data/meetings.sqlite3-wal').exists()
        with sqlite3.connect(tmp_path/'backup/data/meetings.sqlite3') as db:
            assert db.execute("SELECT count(*) FROM meetings WHERE title='wal-only'").fetchone()[0]==1
    finally:connection.close()


def test_running_service_and_worker_lock_refuse_without_creating_a_backup(tmp_path):
    source=tmp_path/'source';sample(source)
    with TestClient(create_app(source,start_worker=False)):
        with pytest.raises(RuntimeError):create_backup(source,tmp_path/'busy')
    assert not (tmp_path/'busy').exists()
    import fcntl
    with (source/'service.lock').open('a') as service:
        fcntl.flock(service,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BackupError):create_backup(source,tmp_path/'legacy-service-busy')
    assert not (tmp_path/'legacy-service-busy').exists()
    with (source/'worker.lock').open('a') as worker:
        fcntl.flock(worker,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BackupError):create_backup(source,tmp_path/'worker-busy')
    assert not (tmp_path/'worker-busy').exists()


def test_maintenance_blocks_store_migration_app_construction_and_second_maintenance(tmp_path):
    source=tmp_path/'source';sample(source)
    before=(source/'meetings.sqlite3').read_bytes()
    with DataLease(source,exclusive=True):
        with pytest.raises(RuntimeError):Store(source)
        with pytest.raises(RuntimeError):create_app(source,start_worker=False)
        with pytest.raises(RuntimeError):create_backup(source,tmp_path/'blocked')
    assert before==(source/'meetings.sqlite3').read_bytes()


@pytest.mark.parametrize('state,source_kind',[('recording','macos'),('paused','macos'),('recording','browser')])
def test_recent_capture_is_not_stopped_by_backup(tmp_path,state,source_kind):
    root=tmp_path/'source';_,_,rid=sample(root);folder=root/'recordings'/rid
    if source_kind=='macos':(folder/'capture.json').write_text(json.dumps({'state':state}))
    else:
        session=json.loads((folder/'session.json').read_text());session.update(state=state);session.pop('source');(folder/'session.json').write_text(json.dumps(session))
    before=(folder/'system.pcm').read_bytes()
    with pytest.raises(BackupError):create_backup(root,tmp_path/'busy')
    assert not (tmp_path/'busy').exists() and before==(folder/'system.pcm').read_bytes()


@pytest.mark.parametrize('damage',['truncate','extra','missing','symlink','path','duplicate','version','database'])
def test_corrupt_or_unsafe_backup_is_rejected_before_restore_target_created(tmp_path,damage):
    source=tmp_path/'source';_,mid,_=sample(source);package=tmp_path/'backup';create_backup(source,package)
    manifest=json.loads((package/'manifest.json').read_text());original=package/'data'/mid/'original'
    if damage=='truncate':original.write_bytes(b'cut')
    elif damage=='extra':(package/'data'/'unexpected').write_bytes(b'x')
    elif damage=='missing':original.unlink()
    elif damage=='symlink':original.unlink();original.symlink_to(source/mid/'original')
    elif damage=='path':manifest['files'][0]['path']='../outside'
    elif damage=='duplicate':manifest['files'].append(manifest['files'][0])
    elif damage=='version':manifest['format_version']=999
    else:
        database=package/'data'/'meetings.sqlite3';database.write_bytes(b'invalid database')
        entry=next(e for e in manifest['files'] if e['path']=='meetings.sqlite3');entry.update(backups.hash_file(database))
    (package/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises((BackupError,sqlite3.Error)):restore_backup(package,tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_source_changes_or_copy_failure_never_publish_partial_backup(tmp_path,monkeypatch):
    source=tmp_path/'source';_,mid,_=sample(source);copy=backups.copy_file
    def changing(src,dst,expected=None):
        result=copy(src,dst,expected)
        if src.name=='original':src.write_bytes(b'concurrent external writer')
        return result
    monkeypatch.setattr(backups,'copy_file',changing)
    with pytest.raises(BackupError):create_backup(source,tmp_path/'changed')
    assert not (tmp_path/'changed').exists()
    def failed(*_):raise OSError('disk full')
    monkeypatch.setattr(backups,'copy_file',failed)
    with pytest.raises(OSError):create_backup(source,tmp_path/'failed')
    assert not (tmp_path/'failed').exists() and (source/mid/'original').exists()


def test_existing_and_nested_targets_are_never_overwritten(tmp_path):
    source=tmp_path/'source';sample(source);package=tmp_path/'backup';create_backup(source,package)
    sentinel=tmp_path/'existing';sentinel.mkdir();(sentinel/'keep').write_text('keep')
    for target in (source,source/'child',package,package/'child',sentinel):
        with pytest.raises(BackupError):restore_backup(package,target)
    assert (sentinel/'keep').read_text()=='keep'
    with pytest.raises(BackupError):create_backup(source,source/'nested')
    with pytest.raises(BackupError):create_backup(source,package)


def test_failed_restore_keeps_marker_and_application_refuses_partial_data(tmp_path,monkeypatch):
    source=tmp_path/'source';sample(source);package=tmp_path/'backup';create_backup(source,package)
    def failed(*_):raise OSError('disk full')
    monkeypatch.setattr(backups,'copy_file',failed);target=tmp_path/'partial'
    with pytest.raises(OSError):restore_backup(package,target)
    assert (target/'.restore-in-progress').exists()
    with pytest.raises(RuntimeError):create_app(target,start_worker=False)
    with pytest.raises(RuntimeError):create_app(package/'data',start_worker=False)
    assert verify_backup(package)['status']=='verified'


def test_source_symlinks_and_unsupported_files_are_not_followed(tmp_path):
    source=tmp_path/'source';sample(source);(source/'link').symlink_to(tmp_path/'outside')
    with pytest.raises(BackupError):create_backup(source,tmp_path/'unsafe')
    assert not (tmp_path/'unsafe').exists()


def test_cli_restore_drill_requires_no_service_and_does_not_import_main(tmp_path):
    source=tmp_path/'source';sample(source);package=tmp_path/'backup';create_backup(source,package)
    report=tmp_path/'report.json'
    result=subprocess.run([sys.executable,'-m','scripts.manage_backups','drill',str(package),'--report',str(report)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    data=json.loads(result.stdout);assert data['status']=='drill_passed' and data['workers_started'] is False
    assert json.loads(report.read_text())==data


def test_cloud_placeholder_preflight_reads_no_content_and_backup_fails_before_copy(tmp_path,monkeypatch):
    from types import SimpleNamespace
    source=tmp_path/'source';sample(source)
    assert backups.is_dataless(SimpleNamespace(st_flags=0x40000000))== (sys.platform=='darwin' or bool(getattr(stat,'SF_DATALESS',0)))
    monkeypatch.setattr(backups,'is_dataless',lambda _:True)
    def forbidden(*_):raise AssertionError('must not read payload')
    monkeypatch.setattr(backups,'sqlite_files',forbidden)
    report=backups.preflight(source)
    assert report['status']=='needs_download' and report['cloud_placeholder_count']>0 and not report['content_read']
    with pytest.raises(BackupError,match='云端占位'):create_backup(source,tmp_path/'backup')
    assert not (tmp_path/'backup').exists()
    assert not (source/'.backup-in-progress').exists()
