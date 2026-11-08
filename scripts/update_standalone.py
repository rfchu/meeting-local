"""Offline manual update and rollback for an installed standalone Tingji.app."""
import argparse
import contextlib
import json
import plistlib
import sqlite3
import subprocess
from pathlib import Path

from app.backups import create_backup, offline
from app.host_bridge import maintenance_report
from scripts.host_delivery import Delivery, DeliveryError, digest_tree, lease


class StandaloneDelivery(Delivery):
    history_name = '.tingji-standalone-releases'

    def __init__(self, target, state, candidate=None, backup=None):
        super().__init__(target)
        self.state = state.resolve(strict=True)
        self.data = self.state / 'data'
        self.candidate = candidate.absolute() if candidate is not None else None
        self.backup = backup.resolve() if backup is not None else None
        self.maintenance = None
        if self.data.is_relative_to(self.target) or self.target.is_relative_to(self.data):
            raise DeliveryError('应用和工作区必须分别存放')
        if self.backup is not None:
            protected = [self.target, self.state, self.history]
            if self.candidate is not None:
                protected.append(self.candidate.resolve())
            if any(self.backup.is_relative_to(path) or path.is_relative_to(self.backup)
                   for path in protected):
                raise DeliveryError('备份必须放在应用、工作区与更新历史之外')

    def verify(self, app):
        digest_tree(app)
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        if (info.get('CFBundleIdentifier') != 'cn.seeknow.tingji.host.standalone'
                or info.get('CFBundleExecutable') != 'TingjiHost'
                or info.get('TingjiPortableRuntime') is not True):
            raise DeliveryError('目标必须为独立版听记')
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app],
                       check=True, capture_output=True)
        if self.target.exists() and app != self.target:
            def team(path):
                result = subprocess.run(['/usr/bin/codesign', '-dv', '--verbose=4', path],
                                        check=True, capture_output=True, text=True)
                return next(line for line in result.stderr.splitlines() if line.startswith('TeamIdentifier='))
            if team(app) != team(self.target):
                raise DeliveryError('签名团队不匹配')
        return info

    def state_dirs(self, app, info):
        return [self.state]

    def apply(self, rollback=None):
        blockers = maintenance_report(self.data)['blockers']
        if blockers:
            raise DeliveryError('更新或回退已延后：' + ', '.join(blockers))
        return super().apply(rollback)

    @contextlib.contextmanager
    def workspace_gate(self):
        database = self.data / 'meetings.sqlite3'
        if not database.is_file():
            raise DeliveryError('未找到工作区数据库')
        with offline(self.data) as self.maintenance, contextlib.closing(sqlite3.connect(
                database.as_uri() + '?mode=rw', uri=True, timeout=0)) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as error:
                raise DeliveryError('工作区数据库正在写入，更新或回退已延后') from error
            yield

    def extra_locks(self):
        return [self.workspace_gate()]

    def compile(self, stage):
        if self.candidate is None:
            raise DeliveryError('请指定已从安装镜像提取的新版应用')
        self.verify(self.candidate)
        subprocess.run(['/usr/bin/ditto', self.candidate, stage], check=True)

    def runtime(self, app):
        runtime = app / 'Contents/Resources/PortableRuntime'
        result = subprocess.check_output(
            [runtime / 'python', 'backend', '-c',
             'import json,sys; from app.migrations import inspect_database; '
             'print(json.dumps(inspect_database(sys.argv[1])))',
             self.data / 'meetings.sqlite3'], cwd=self.target.parent, text=True, stderr=subprocess.PIPE)
        return json.loads(result)

    def publish(self, stage, record):
        if record['action'] == 'build':
            if self.backup is None:
                raise DeliveryError('更新前必须指定新的备份目录')
            create_backup(self.data, self.backup, maintenance=self.maintenance)
            record['backup'] = str(self.backup.resolve())
        return super().publish(stage, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['update', 'rollback', 'recover'])
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--release')
    args = parser.parse_args()
    if args.action == 'rollback' and not args.release:
        parser.error('回退需要 --release')
    try:
        delivery = StandaloneDelivery(args.app, args.state, args.candidate, args.backup)
        if not delivery.target.is_dir():
            raise DeliveryError('请指定已安装的独立版应用')
        if args.action == 'recover':
            with lease(delivery.history, 'delivery.lock'):
                result = delivery.recover()
        else:
            result = delivery.apply(args.release if args.action == 'rollback' else None)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
