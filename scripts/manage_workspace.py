"""Workspace inspection, verified migration staging and guarded desktop delivery."""
import argparse
import json
import sqlite3
import subprocess
from pathlib import Path

from app import backups, workspace_readiness
from app.paths import RuntimePaths
from app.migrations import inspect_database
from app.release_readiness import frontend_delivery, inspect_release
from scripts.host_delivery import DEFAULT_TARGET, Delivery
from scripts.capture_delivery import CaptureDelivery


def delivery_status(delivery):
    try:
        return delivery.status()
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state': 'requires_check', 'message': '组件或内置运行配置需要核对'}


def main():
    paths = RuntimePaths.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('check')
    for name in ('plan', 'stage'):
        command = commands.add_parser(name)
        command.add_argument('--backup', required=True)
        command.add_argument('--target', required=True)
    verify = commands.add_parser('verify-target')
    verify.add_argument('--backup', required=True)
    verify.add_argument('--target', required=True)
    commands.add_parser('desktop-status')
    for name in ('desktop-build', 'desktop-rollback'):
        desktop = commands.add_parser(name)
        desktop.add_argument('--component', choices=('host', 'capture'), default='host')
        if name == 'desktop-rollback':
            desktop.add_argument('--release', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'check':
            result = workspace_readiness.inspect(paths)
        elif args.action in ('plan', 'stage'):
            result = workspace_readiness.migration_plan(paths.data, args.backup, args.target)
            if args.action == 'stage':
                if not result['ready']:
                    raise backups.BackupError('请先下载所有云端占位文件')
                with backups.offline(paths.data) as lease:
                    backups.create_backup(paths.data, result['backup'], maintenance=lease)
                    result['restore'] = backups.restore_backup(result['backup'], result['target'])
        elif args.action == 'verify-target':
            target = Path(args.target).expanduser().resolve()
            if not (target/'meetings.sqlite3').is_file():
                raise backups.BackupError('恢复目标尚无会议数据库')
            with backups.offline(target):
                manifest = backups.load_manifest(Path(args.backup).expanduser())
                backups.verify_data(target, manifest)
                schema = inspect_database(target/'meetings.sqlite3')
                if schema['needs_upgrade']:
                    raise backups.BackupError('目标数据库需要离线升级')
            result = {'status': 'target_verified', 'data_dir': str(target),
                      'rollback_data_dir': manifest['source_root'], 'schema': schema}
        else:
            host = Delivery(DEFAULT_TARGET)
            capture = CaptureDelivery(DEFAULT_TARGET.with_name('TingjiCapture.app'))
            if args.action == 'desktop-status':
                result = {'host_delivery': delivery_status(host), 'capture_delivery': delivery_status(capture),
                          'release': inspect_release(paths.repository),
                          'database': inspect_database(paths.data/'meetings.sqlite3')}
            else:
                delivery = capture if args.component == 'capture' else host
                if not (paths.data/'meetings.sqlite3').is_file():
                    raise backups.BackupError('请先核对当前运行数据目录')
                with backups.offline(paths.data):
                    if inspect_database(paths.data/'meetings.sqlite3')['needs_upgrade']:
                        raise backups.BackupError('当前数据需要离线升级')
                    if not frontend_delivery(paths.repository)['verified']:
                        raise backups.BackupError('前端构建产物不完整，请先构建')
                    result = delivery.apply(getattr(args, 'release', None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f'工作空间操作未完成：{error}\n')


if __name__ == '__main__':
    main()
