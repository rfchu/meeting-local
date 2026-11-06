"""Offline upgrades: one maintenance lease spans backup, full rehearsal and commit."""
import tempfile
from pathlib import Path
from .backups import (BackupError,offline,preflight,require_space,create_backup,load_manifest,
    restore_backup,verify_backup,scan,hash_file,logical_inventory,write_json)
from .migrations import inspect_database,migrate_database


def migration_drill(bundle):
    bundle=Path(bundle);manifest=load_manifest(bundle)
    with tempfile.TemporaryDirectory(prefix='tingji-migration-drill-') as folder:
        target=Path(folder)/'restored'
        restore_backup(bundle,target)
        before=inspect_database(target/'meetings.sqlite3')
        result=migrate_database(target/'meetings.sqlite3')
        after=inspect_database(target/'meetings.sqlite3')
        if after['needs_upgrade']:raise BackupError('升级演练后版本未达到预期')
        for entry in manifest['files']:
            if entry['path']!='meetings.sqlite3' and hash_file(target/entry['path'])!={'size':entry['size'],'sha256':entry['sha256']}:
                raise BackupError('升级演练改变了非目标文件')
        if logical_inventory(target,manifest['files'])!=manifest['logical_inventory']:raise BackupError('升级演练前后会议数据计数不一致')
    verify_backup(bundle)
    return {'status':'migration_drill_passed','from_version':before['current_version'],
            'to_version':after['current_version'],'migration_status':result['status'],
            'temporary_restore_removed':True,'workers_started':False,'source_overwritten':False}


def upgrade_data(source,backup_output,code_version=None):
    root=Path(source).resolve();bundle=Path(backup_output).resolve()
    if not root.is_dir() or not (root/'meetings.sqlite3').is_file():raise BackupError('源数据目录无效，未创建或修改数据库')
    with offline(root) as lease:
        state=inspect_database(root/'meetings.sqlite3')
        if not state['needs_upgrade']:return dict(state,status='already_current',backup_created=False)
        readiness=preflight(root)
        if readiness['cloud_placeholder_count']:raise BackupError('升级前仍有云端占位文件，请先下载到本机')
        # Backup and temporary rehearsal must coexist on their respective filesystems.
        require_space(bundle.parent,readiness['estimated_bytes'])
        require_space(Path(tempfile.gettempdir()),readiness['estimated_bytes'])
        parent=bundle.parent
        while not parent.exists():parent=parent.parent
        if parent.stat().st_dev==Path(tempfile.gettempdir()).stat().st_dev:
            require_space(bundle.parent,2*readiness['estimated_bytes'])
        backup=create_backup(root,bundle,code_version,maintenance=lease)
        stage='rehearsal'
        try:
            before=scan(root)
            rehearsal=migration_drill(bundle)
            if scan(root)!=before:raise BackupError('预演期间源数据发生变化，保留备份且未升级')
            stage='apply'
            result=migrate_database(root/'meetings.sqlite3')
        except BaseException:
            try:write_json(bundle/'upgrade-failure.json',{'status':'upgrade_failed','stage':stage,'backup_preserved':True})
            except OSError:pass
            raise
        report=dict(result,backup_directory=str(bundle),backup_verification=backup,
                    rehearsal=rehearsal,maintenance_lock_held_through_commit=True,workers_started=False)
        try:write_json(bundle/'upgrade-report.json',report);report['report_saved']=True
        except OSError:report['report_saved']=False
        return report
