"""Versioned upgrade tests use temporary data only, with no model execution."""
import hashlib,json,sqlite3,subprocess,sys
from pathlib import Path
import pytest
from app.db import Store
from app.maintenance import DataLease
from app.backups import create_backup,verify_backup,BackupError
from app.migrations import (LATEST_VERSION,MigrationError,inspect_database,migrate_database,
                            initialize_store)
from app.schema_v21 import execute_script
from app.upgrades import upgrade_data,migration_drill
import app.migrations as migrations
import app.upgrades as upgrades


def legacy(root,version=21):
    store=Store(root)
    with store.connect() as db:
        db.execute('DROP TABLE schema_migrations');db.execute(f'PRAGMA user_version={version}')
        db.execute("INSERT INTO meetings(id,title,filename,segments) VALUES(?,?,?,?)",('a'*32,'PRIVATE-MEETING','sample.wav','[{"text":"PRIVATE-TEXT"}]'))
    folder=root/('a'*32);folder.mkdir();audio=b'local-synthetic-audio';(folder/'original').write_bytes(audio)
    (folder/'asset.json').write_text(json.dumps({'size':len(audio),'sha256':hashlib.sha256(audio).hexdigest()}))
    return root/'meetings.sqlite3'


def test_new_store_creates_versioned_ledger_and_reopen_is_idempotent(tmp_path):
    store=Store(tmp_path/'data')
    assert inspect_database(store.path)['current_version']==LATEST_VERSION
    rows=store.all('SELECT * FROM schema_migrations ORDER BY version')
    assert [r['mode'] for r in rows]==['applied','applied']
    Store(store.root)
    assert store.all('SELECT * FROM schema_migrations ORDER BY version')==rows


def test_legacy_startup_refuses_before_mutation_and_status_is_read_only(tmp_path):
    root=tmp_path/'data';path=legacy(root);before=path.read_bytes()
    assert inspect_database(path)['pending_versions']==[22]
    assert path.read_bytes()==before
    with pytest.raises(MigrationError,match='离线升级'):Store(root)
    assert path.read_bytes()==before


@pytest.mark.parametrize('version',[0,17,20,21])
def test_upgrade_backups_rehearses_and_preserves_originals_and_rows(tmp_path,version):
    root=tmp_path/'data';path=legacy(root,version)
    if version<21:
        with sqlite3.connect(path) as db:db.execute('ALTER TABLE meeting_notes DROP COLUMN overview_focus')
    result=upgrade_data(root,tmp_path/'nested'/'backup')
    assert result['status']=='migrated' and result['from_version']==version
    assert result['rehearsal']['status']=='migration_drill_passed' and result['maintenance_lock_held_through_commit']
    assert inspect_database(path)['ledger']=='verified'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT title FROM meetings').fetchone()[0]=='PRIVATE-MEETING'
        assert db.execute('SELECT mode FROM schema_migrations WHERE version=21').fetchone()[0]==('adopted' if version==21 else 'applied')
    bundle=tmp_path/'nested'/'backup'
    assert verify_backup(bundle)['logical_inventory']['meeting_count']==1
    assert (bundle/'data'/('a'*32)/'original').read_bytes()==(root/('a'*32)/'original').read_bytes()
    assert upgrade_data(root,tmp_path/'unused')['status']=='already_current' and not (tmp_path/'unused').exists()
    assert Store(root).one('PRAGMA user_version')['user_version']==22


@pytest.mark.parametrize('damage',['future','missing_table','missing_index','missing_trigger','column','ledger','ledger_missing','ledger_version'])
def test_future_or_mismatched_schema_is_rejected_without_writes(tmp_path,damage):
    store=Store(tmp_path/'data')
    with store.connect() as db:
        if damage=='future':db.execute('PRAGMA user_version=99')
        elif damage=='missing_table':db.execute('DROP TABLE model_preferences')
        elif damage=='missing_index':db.execute('DROP INDEX job_runtime_events_job')
        elif damage=='missing_trigger':db.execute('DROP TRIGGER job_runtime_events_cleanup')
        elif damage=='column':db.execute('ALTER TABLE model_preferences RENAME COLUMN last_value TO wrong_value')
        elif damage=='ledger':db.execute("UPDATE schema_migrations SET checksum='wrong' WHERE version=22")
        elif damage=='ledger_missing':db.execute('DROP TABLE schema_migrations')
        else:db.execute('PRAGMA user_version=21')
    before=store.path.read_bytes()
    with pytest.raises(MigrationError):Store(store.root)
    with pytest.raises(MigrationError):upgrade_data(store.root,tmp_path/'backup')
    assert not (tmp_path/'backup').exists() and store.path.read_bytes()==before


def test_statement_executor_keeps_trigger_and_ddl_in_same_rollback(tmp_path):
    path=tmp_path/'db.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        execute_script(db,'CREATE TABLE a(id); CREATE TABLE log(id); CREATE TRIGGER log_a AFTER INSERT ON a BEGIN INSERT INTO log VALUES(NEW.id); END;')
        db.execute('INSERT INTO a VALUES(1)');assert db.execute('SELECT id FROM log').fetchone()[0]==1
        db.rollback()
        assert db.execute("SELECT name FROM sqlite_master WHERE name='a'").fetchone() is None


def test_apply_failure_rolls_back_schema_version_and_data_and_keeps_backup(tmp_path,monkeypatch):
    root=tmp_path/'data';path=legacy(root);before=path.read_bytes();real=migrations.migrate_connection
    def failing(db):
        result=real(db)
        if Path(db.execute('PRAGMA database_list').fetchone()[2])==path:
            db.execute("UPDATE meetings SET title='changed'");raise RuntimeError('injected failure before commit')
        return result
    monkeypatch.setattr(migrations,'migrate_connection',failing)
    with pytest.raises(RuntimeError):upgrade_data(root,tmp_path/'backup')
    assert inspect_database(path)['current_version']==21
    assert path.read_bytes()==before
    assert verify_backup(tmp_path/'backup')['status']=='verified'
    assert json.loads((tmp_path/'backup'/'upgrade-failure.json').read_text())['stage']=='apply'


def test_rehearsal_failure_never_applies_and_preserves_backup(tmp_path,monkeypatch):
    root=tmp_path/'data';path=legacy(root);before=path.read_bytes()
    def fail(_):raise RuntimeError('injected rehearsal failure')
    monkeypatch.setattr(upgrades,'migration_drill',fail)
    with pytest.raises(RuntimeError):upgrade_data(root,tmp_path/'backup')
    assert before==path.read_bytes() and verify_backup(tmp_path/'backup')['status']=='verified'
    assert json.loads((tmp_path/'backup'/'upgrade-failure.json').read_text())['stage']=='rehearsal'


def test_single_maintenance_lock_spans_backup_rehearsal_and_commit(tmp_path,monkeypatch):
    root=tmp_path/'data';legacy(root);real=upgrades.migration_drill
    def guarded(bundle):
        with pytest.raises(RuntimeError):Store(root)
        with pytest.raises(RuntimeError):create_backup(root,tmp_path/'competing')
        return real(bundle)
    monkeypatch.setattr(upgrades,'migration_drill',guarded)
    upgrade_data(root,tmp_path/'backup')
    with DataLease(root):
        with pytest.raises(RuntimeError):upgrade_data(root,tmp_path/'busy')
    assert not (tmp_path/'busy').exists()


def test_external_source_change_after_rehearsal_blocks_apply(tmp_path,monkeypatch):
    root=tmp_path/'data';path=legacy(root);real=upgrades.migration_drill
    def changed(bundle):
        result=real(bundle);(root/'external-change').write_text('synthetic');return result
    monkeypatch.setattr(upgrades,'migration_drill',changed)
    with pytest.raises(BackupError,match='源数据发生变化'):upgrade_data(root,tmp_path/'backup')
    assert inspect_database(path)['current_version']==21


def test_space_failure_precedes_backup_or_schema_write(tmp_path,monkeypatch):
    root=tmp_path/'data';path=legacy(root);before=path.read_bytes()
    def fail(*_):raise BackupError('disk full')
    monkeypatch.setattr(upgrades,'require_space',fail)
    with pytest.raises(BackupError):upgrade_data(root,tmp_path/'backup')
    assert before==path.read_bytes() and not (tmp_path/'backup').exists()


def test_cli_drill_no_main_import_no_source_mutation(tmp_path):
    root=tmp_path/'data';path=legacy(root);bundle=tmp_path/'backup';create_backup(root,bundle)
    before=path.read_bytes();report=tmp_path/'drill-report.json'
    process=subprocess.run([sys.executable,'-m','scripts.manage_migrations','drill',str(bundle),'--report',str(report)],capture_output=True,text=True,timeout=15)
    assert process.returncode==0,process.stderr
    result=json.loads(process.stdout);assert result['status']=='migration_drill_passed' and not result['workers_started']
    assert before==path.read_bytes() and json.loads(report.read_text())==result
    assert 'app.main' not in Path('scripts/manage_migrations.py').read_text().split('\n',1)[1]


def test_legacy_upgrade_cannot_silently_mutate_existing_business_rows(tmp_path,monkeypatch):
    root=tmp_path/'data';path=legacy(root,17);before=path.read_bytes();real=migrations.install_legacy_schema
    def mutated(db):
        real(db);db.execute("UPDATE meetings SET title='injected mutation'")
    monkeypatch.setattr(migrations,'install_legacy_schema',mutated)
    with pytest.raises(MigrationError,match='业务数据'):migrate_database(path)
    assert path.read_bytes()==before and inspect_database(path)['current_version']==17


def test_report_write_failure_reports_committed_upgrade_instead_of_false_rollback(tmp_path,monkeypatch):
    root=tmp_path/'data';path=legacy(root)
    def no_report(*_):raise OSError('report disk error')
    monkeypatch.setattr(upgrades,'write_json',no_report)
    result=upgrade_data(root,tmp_path/'backup')
    assert result['status']=='migrated' and not result['report_saved']
    assert inspect_database(path)['current_version']==22 and verify_backup(tmp_path/'backup')['status']=='verified'
