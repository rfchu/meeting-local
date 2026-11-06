"""Local macOS host delivery. Never installs dependencies, migrates data or controls services.

The signed app remains source-bound: Python, models, data and connection settings are
external. Release history is local, private, and intentionally not garbage-collected.
"""
import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = 'cn.seeknow.tingji.host'
VERSION = '0.4.0'
DEFAULT_TARGET = Path.home()/'Library/Caches/cn.seeknow.tingji/Tingji.app'
DEFAULT_STATE = Path.home()/'Library/Application Support/cn.seeknow.tingji.host'


class DeliveryError(ValueError):
    pass


def read_json(path):
    if path.is_symlink() or path.stat().st_size > 65536:
        raise DeliveryError('交付记录不安全或过大')
    return json.loads(path.read_text())


def write_json(path, value):
    temporary = path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temporary, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
            json.dump(value, f, ensure_ascii=False, indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def private_folder(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise DeliveryError('交付与宿主状态目录必须属于当前用户且权限为 0700')


@contextlib.contextmanager
def lease(folder, filename):
    private_folder(folder)
    fd = os.open(folder/filename, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DeliveryError('锁文件权限不安全')
        try:fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise DeliveryError('旧宿主或另一交付操作仍在运行，请先退出；未替换应用') from None
        yield
    finally:os.close(fd)


def digest_tree(app):
    digest = hashlib.sha256()
    for path in sorted(app.rglob('*')):
        if path.is_symlink():raise DeliveryError('应用包不允许包含符号链接')
        if path.is_file():
            digest.update(str(path.relative_to(app)).encode());digest.update(b'\0')
            digest.update(path.read_bytes());digest.update(b'\0')
    return digest.hexdigest()


def runtime_digest(root):
    digest = hashlib.sha256()
    for path in sorted([*root.glob('app/**/*.py'), *root.glob('requirements*.txt')]):
        digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def manifest(app):
    config = read_json(app/'Contents/Resources/host-config.json')
    try:
        commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL))
    except (OSError,subprocess.SubprocessError):commit,dirty = 'unavailable',None
    write_json(app/'Contents/Resources/delivery.json',{
        'format':1,'host_version':VERSION,'protocol_version':1,'schema_versions':[22],
        'source_commit':commit,'source_dirty':dirty,'runtime_digest':runtime_digest(Path(config['projectRoot'])),
        'ownership':{'app':'host_ui','python':'external_project_venv','models':'external_local_or_provider',
                     'data':'external_workspace','settings':'user_application_support'},
        'service_control':False,'capture_owner':False,'distribution':'local_adhoc_source_bound'})


def bundle_info(app):
    if app.is_symlink() or not app.is_dir():raise DeliveryError('应用不存在或是符号链接')
    with (app/'Contents/Info.plist').open('rb') as f:info=plistlib.load(f)
    if info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleExecutable') != 'TingjiHost':
        raise DeliveryError('应用身份不匹配')
    return info


def verify_bundle(app):
    if platform.system() != 'Darwin':raise DeliveryError('本机宿主交付仅支持 macOS')
    if tuple(map(int,platform.mac_ver()[0].split('.')[:1])) < (15,):raise DeliveryError('本机宿主需要 macOS 15+')
    info = bundle_info(app)
    # Walk first: never ask tools to follow unexpected bundle links.
    digest_tree(app)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
    architectures = subprocess.check_output(['lipo','-archs',str(app/'Contents/MacOS/TingjiHost')],text=True).split()
    if 'arm64' not in architectures:raise DeliveryError('应用不支持 Apple Silicon')
    if info.get('CFBundleShortVersionString') not in ('0.3.0',VERSION):
        raise DeliveryError('宿主版本不在本轮支持范围内，未覆盖')
    return info


def config_for(app):
    value = read_json(app/'Contents/Resources/host-config.json')
    if value.get('version') != 1 or type(value.get('port')) is not int or not 1024 <= value['port'] <= 65535:
        raise DeliveryError('内置连接版本或端口无效')
    for key in ('projectRoot','dataRoot','backupsRoot'):
        if not isinstance(value.get(key),str) or not Path(value[key]).is_absolute() or any(c in value[key] for c in ('\n','\0')):
            raise DeliveryError('内置连接目录无效')
    return value


def runtime_check(app):
    config = config_for(app);root = Path(config['projectRoot']);python = root/'.venv/bin/python'
    if not python.is_file() or not (root/'scripts/manage_service.py').is_file():
        raise DeliveryError('源码或 Python 环境缺失；本机宿主不是独立运行环境')
    probe = "import importlib.util,json,sys;print(json.dumps({'python':list(sys.version_info[:2]),'dependencies':all(importlib.util.find_spec(x) is not None for x in ('fastapi','uvicorn','pydantic'))}))"
    result = json.loads(subprocess.check_output([str(python),'-I','-c',probe],text=True,timeout=15))
    if tuple(result['python']) < (3,12) or not result['dependencies']:raise DeliveryError('Python 需 3.12+ 且基础依赖已安装')
    metadata = app/'Contents/Resources/delivery.json'
    legacy = not metadata.exists()
    if not legacy:
        release = read_json(metadata)
        if release.get('format') != 1 or release.get('protocol_version') != 1 or release.get('runtime_digest') != runtime_digest(root):
            raise DeliveryError('应用与当前源码运行环境不匹配，请从当前源码重新构建')
    database = Path(config['dataRoot'])/'meetings.sqlite3'
    schema = None
    if database.exists():
        import sqlite3
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as db:
            schema = db.execute('PRAGMA user_version').fetchone()[0]
        supported = [22] if legacy else release.get('schema_versions',[])
        if schema not in supported:raise DeliveryError('数据库版本不兼容，未迁移或替换数据')
    return {'python':result['python'],'schema_version':schema,'legacy_manifest':legacy,
            'runtime':'external_source_bound','models_bundled':False,'data_changed':False,
            'local_model_directory_present':(root/'models').is_dir(),
            'qwen_environment_present':(root/'.venv-qwen/bin/python').is_file(),
            'runtime_scope':'bundled_defaults','saved_connection':'preserved_not_rewritten','signature':'local_adhoc_not_notarized'}


def state_directories(app, info):
    values = {Path(os.getenv('TINGJI_HOST_STATE_DIR',str(info.get('TingjiHostStateDirectory',DEFAULT_STATE))))}
    if app.exists():values.add(Path(info.get('TingjiHostStateDirectory',DEFAULT_STATE)))
    if info.get('TingjiHostStateDirectory'):values.add(Path(info['TingjiHostStateDirectory']))
    for value in values:
        if not value.is_absolute():raise DeliveryError('宿主配置目录必须是绝对路径')
    return sorted(values)


def reject_running(app):
    # Protect alternate state-directory invocations as well as the cooperative flock.
    output = subprocess.check_output(['ps','-axo','command='],text=True,timeout=5)
    executable = app/'Contents/MacOS/TingjiHost'
    aliases = {str(executable),str(executable.resolve())}
    if str(executable).startswith(('/private/tmp/','/private/var/')):
        aliases.add(str(executable).removeprefix('/private'))
    if any(any(line == p or line.startswith(p+' ') for p in aliases) for line in output.splitlines()):
        raise DeliveryError('目标应用仍在运行，请先保存并退出宿主；不会结束其进程或服务')


def exchange(a,b):
    # APFS same-volume atomic directory exchange, no moment with a missing active app.
    if platform.system() != 'Darwin':raise DeliveryError('交付替换仅支持 macOS')
    libc = ctypes.CDLL(None,use_errno=True)
    if libc.renamex_np(os.fsencode(a),os.fsencode(b),2) != 0:
        raise OSError(ctypes.get_errno(),'原子应用替换失败')


class Delivery:
    def __init__(self,target):
        raw = Path(target).expanduser().absolute()
        if raw.name != 'Tingji.app' or raw.is_symlink():raise DeliveryError('目标必须是非符号链接的 Tingji.app')
        self.target = raw.parent.resolve()/raw.name
        # Refuse any target that could replace the project or workspace itself.
        if self.target == ROOT or ROOT.is_relative_to(self.target):raise DeliveryError('不能覆盖源码目录')
        self.history = self.target.parent/'.tingji-host-releases'
        self.journal = self.history/'transaction.json'

    def status(self):
        report = {'target_exists':self.target.exists(),'host_version':None,'history':[],
                  'pending_transaction':False,'automatic_service_control':False,'data_or_models_bundled':False}
        if self.target.exists():
            info = verify_bundle(self.target);report['host_version']=info['CFBundleShortVersionString']
            report['runtime']=runtime_check(self.target)
        if self.journal.exists():report['pending_transaction']=read_json(self.journal).get('state')=='prepared'
        active_digest = digest_tree(self.target) if self.target.exists() else None
        if self.history.exists():
            for path in sorted(self.history.glob('*/release.json')):
                record = read_json(path);archived = self.stage_path(path.parent.name)
                available = bool(record.get('state')=='published' and record.get('old_digest') and active_digest==record.get('new_digest') and archived.is_dir() and digest_tree(archived)==record['old_digest'])
                report['history'].append({'id':path.parent.name,'state':record['state'],'retained_previous':record.get('old_digest') is not None,'rollback_available':available})
        return report

    def recover(self):
        if not self.journal.exists():return {'state':'none'}
        record = read_json(self.journal)
        if record.get('state') != 'prepared':return {'state':record.get('state')}
        stage = self.stage_path(record['id'])
        active = digest_tree(self.target) if self.target.exists() else None
        retained = digest_tree(stage) if stage.exists() else None
        if active == record['new_digest'] and retained == record['old_digest']:record['state']='published'
        elif active == record['old_digest'] and retained == record['new_digest']:record['state']='aborted'
        else:raise DeliveryError('交付中断后的文件与记录不一致，保留现场，未覆盖应用')
        write_json(stage.parent/'release.json',record);write_json(self.journal,record)
        return {'state':record['state'],'release':record['id']}

    def stage_path(self,release):
        if not isinstance(release,str) or len(release)!=32 or any(c not in '0123456789abcdef' for c in release):raise DeliveryError('回退版本编号无效')
        folder=self.history/release
        if folder.is_symlink():raise DeliveryError('版本目录不能是符号链接')
        return folder/'Tingji.app'

    def publish(self,stage,record):
        old_exists=self.target.exists()
        record.update(state='prepared',new_digest=digest_tree(stage),old_digest=digest_tree(self.target) if old_exists else None)
        write_json(self.journal,record)
        if old_exists:exchange(stage,self.target)
        else:os.rename(stage,self.target)
        for directory in (self.target.parent,stage.parent):
            fd=os.open(directory,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        # A failure here is recoverable from the prepared journal and actual tree hashes.
        record['state']='published';write_json(stage.parent/'release.json',record);write_json(self.journal,record)
        return {'state':'published','release':record['id'],'rollback_available':old_exists,'service_restarted':False,'data_changed':False}

    def apply(self,rollback=None):
        with lease(self.history,'delivery.lock'):
            if self.journal.exists() and read_json(self.journal).get('state')=='prepared':
                raise DeliveryError('存在未完成交付，请先执行 recover 核对，未开始新替换')
            info = verify_bundle(self.target) if self.target.exists() else {}
            reject_running(self.target)
            with contextlib.ExitStack() as locks:
                directories = set(state_directories(self.target,info))
                # New defaults may use a different test state directory; hold both through publication.
                for directory in sorted(directories):locks.enter_context(lease(directory,'host.lock'))
                release = uuid.uuid4().hex;stage=self.stage_path(release);private_folder(stage.parent)
                if rollback:
                    archived=self.stage_path(rollback);record=read_json(archived.parent/'release.json')
                    if record.get('state')!='published' or not record.get('old_digest') or digest_tree(archived)!=record['old_digest']:
                        raise DeliveryError('旧版本缺失或已变化，未回退')
                    if digest_tree(self.target)!=record['new_digest']:raise DeliveryError('当前应用已变化，不能应用这条回退记录')
                    shutil.copytree(archived,stage)
                else:
                    env=os.environ.copy();env['TINGJI_HOST_COMPILE_APP']=str(stage)
                    subprocess.run(['bash',str(ROOT/'scripts/compile_host.sh')],cwd=ROOT,env=env,check=True)
                verify_bundle(stage);runtime_check(stage)
                for directory in state_directories(stage,bundle_info(stage)):
                    if directory not in directories:locks.enter_context(lease(directory,'host.lock'));directories.add(directory)
                reject_running(self.target)
                return self.publish(stage,{'id':release,'action':'rollback' if rollback else 'build','from_release':rollback})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','status','rollback','recover','manifest'])
    parser.add_argument('--target',type=Path,default=DEFAULT_TARGET)
    parser.add_argument('--release');parser.add_argument('--app',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='manifest':
            if not args.app or args.app.parent.parent.name!='.tingji-host-releases':raise DeliveryError('仅可为隔离构建写入清单')
            manifest(args.app);return
        delivery=Delivery(args.target)
        if args.action=='status':value=delivery.status()
        elif args.action=='recover':
            with lease(delivery.history,'delivery.lock'):value=delivery.recover()
        else:
            if args.action=='rollback' and not args.release:raise DeliveryError('回退必须指定 --release 编号')
            value=delivery.apply(args.release if args.action=='rollback' else None)
        print(json.dumps(value,ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        # Subprocess diagnostics may contain local paths, so do not dump captured output.
        parser.exit(1,('交付操作未完成：'+str(error)+'\n'))

if __name__=='__main__':main()
