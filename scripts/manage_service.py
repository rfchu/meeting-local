"""Standalone service, with optional current-login launchd management.

The HTTP/capture process belongs to launchd rather than a development terminal.
"""
import sys,argparse,json,os,platform,plistlib,re,socket,sqlite3,subprocess,time,uuid,signal,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.paths import RuntimePaths
LABEL='cn.seeknow.tingji.local'
RESTART_ERRORS = {
    'capture_active': '仍在录音或写入音频，请回到本场结束录音。',
    'capture_unverified': '录音状态待确认，请在工作台核对录音恢复。',
    'jobs_active': '后台任务仍在运行或排队，请在运行管理中查看。',
    'service_unverified': '无法核对后台实例，请重新检查连接。',
    'stop_timeout': '原后台尚未完全退出，请在服务状态中重新检查。',
    'start_timeout': '已请求启动，但后台尚未确认就绪，请查看运行日志。',
}


class RestartBlocked(ValueError):
    def __init__(self, reasons):
        self.reasons = reasons
        super().__init__(' '.join(RESTART_ERRORS[reason] for reason in reasons))



class MacService:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.paths=RuntimePaths.from_env(root=self.root);self.data=self.paths.data
        self.portable=os.getenv('TINGJI_PORTABLE_SERVICE') == '1'
        self.port=int(os.getenv('PORT','8765'))
        if not 1024<=self.port<=65535:raise ValueError('PORT 必须在 1024～65535 之间')
        self.domain=f'gui/{os.getuid()}';self.target=self.domain+'/'+LABEL
        self.folder=self.data/'services';self.plist=self.folder/(LABEL+'.plist')
        self.record=self.folder/'detached.json'

    def call(self,*args):
        return subprocess.run(['launchctl',*args],capture_output=True,text=True,timeout=35)

    def status(self):
        detached=self.detached_status()
        if detached:return detached
        if self.portable:
            return {'loaded':False,'running':False,'port':self.port}
        result=self.call('print',self.target)
        if result.returncode:return {'loaded':False,'running':False,'port':self.port}
        def field(pattern):
            found=re.search(pattern,result.stdout,re.M);return found.group(1) if found else None
        pid=field(r'^\s*pid = (\d+)$');code=field(r'^\s*last exit code = (-?\d+)(?::[^\n]*)?$')
        return {'loaded':True,'running':bool(pid),'ready':bool(pid and self.identity().get('pid')==int(pid)),'backend':'launchd','pid':int(pid) if pid else None,
                'state':field(r'^\s*state = ([^\n]+)$'),'last_exit_code':int(code) if code else None,'port':self.port}

    def identity(self):
        try:
            url = f'http://127.0.0.1:{self.port}/api/runtime/service'
            if not self.portable:
                with urllib.request.urlopen(url,timeout=2) as reply:return json.load(reply)
            from hmac import compare_digest
            from app.host_bridge import read_workspace_key, workspace_mac
            challenge = uuid.uuid4().hex
            request = urllib.request.Request(url, headers={'X-Tingji-Challenge': challenge})
            with urllib.request.urlopen(request,timeout=2) as reply:
                if reply.geturl() != url:return {}
                identity = json.loads(reply.read(16385))
            proof = identity.get('proof', '')
            return identity if isinstance(proof, str) and proof.isascii() and compare_digest(
                proof, workspace_mac(read_workspace_key(self.paths), 'service:' + challenge)) else {}
        except (OSError,ValueError,AttributeError):return {}

    def detached_status(self):
        pid=None
        try:
            record=json.loads(self.record.read_text());pid=record['pid']
            if type(pid) is not int or pid<=1:return None
            os.kill(pid,0)
        except PermissionError:
            return {'loaded':True,'running':None,'ready':False,'pid':pid,'backend':'detached','port':self.port,'error':'进程状态不可访问，不能判断服务已停止'}
        except (OSError,ValueError,KeyError):return None
        identity=self.identity()
        ready=identity.get('instance_id')==record.get('instance_id') and identity.get('pid')==pid
        return {'loaded':True,'running':True,'ready':ready,'pid':pid,'backend':'detached','port':self.port}

    def occupied(self):
        try:
            with socket.create_connection(('127.0.0.1',self.port),timeout=.5):return True
        except OSError:return False

    def start(self,detached=False):
        status=self.status()
        if status['running']:return status
        if status.get('backend')=='detached' and status['running'] is None:raise ValueError('无法核对已有进程，未启动第二个服务')
        if self.occupied():raise ValueError(f'端口 {self.port} 已被其他进程占用，未启动第二个服务')
        python=self.root/'.venv/bin/python'
        if not python.is_file():raise ValueError('请先完成本机 Python 环境安装')
        if detached or self.portable:
            if status['loaded']:
                result=self.call('bootout',self.target)
                if result.returncode:raise RuntimeError('旧托管实例尚未卸载，未启动第二个服务')
            self.folder.mkdir(parents=True,exist_ok=True);os.chmod(self.folder,0o700)
            instance=uuid.uuid4().hex;env=os.environ.copy()
            env.update(MEETING_DATA=str(self.data),TINGJI_INSTANCE_ID=instance,TINGJI_SERVICE_MODE='detached',PYTHONUNBUFFERED='1')
            # Finder can replace the app bundle while this detached process is alive.
            # Keep its working directory outside the bundle and import from the trusted app path.
            working_directory = self.data if self.portable else self.root
            python_options = ['-P'] if self.portable else []
            command = [str(python), *python_options, '-m', 'uvicorn', 'app.main:app',
                       '--app-dir', str(self.root), '--host', '127.0.0.1', '--port', str(self.port),
                       '--timeout-graceful-shutdown', '20']
            with (self.data/'server.log').open('ab') as log:
                process=subprocess.Popen(command,cwd=working_directory,stdin=subprocess.DEVNULL,
                    stdout=log,stderr=log,env=env,start_new_session=True,close_fds=True)
            temporary=self.record.with_suffix('.tmp')
            with temporary.open('w') as handle:
                json.dump({'pid':process.pid,'instance_id':instance,'started':time.time()},handle);handle.flush();os.fsync(handle.fileno())
            os.chmod(temporary,0o600);temporary.replace(self.record)
            return {'loaded':True,'running':process.poll() is None,'ready':False,'pid':process.pid,'backend':'detached','port':self.port}
        if status['loaded']:
            result=self.call('kickstart',self.target)
        else:
            self.folder.mkdir(parents=True,exist_ok=True);os.chmod(self.folder,0o700)
            definition={'Label':LABEL,'WorkingDirectory':str(self.root),
                'ProgramArguments':[str(python),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(self.port),'--timeout-graceful-shutdown','20'],
                'EnvironmentVariables':{'PYTHONUNBUFFERED':'1','MEETING_DATA':str(self.data),'TINGJI_SERVICE_MODE':'launchd'},
                'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':10,'ExitTimeOut':30,
                'StandardOutPath':str(self.data/'server.log'),'StandardErrorPath':str(self.data/'server.log')}
            for key in ('MEETING_BACKUPS','MEETING_MODELS','WHISPER_CLI','WHISPER_MODEL',
                        'QWEN_PYTHON','QWEN_ASR_MODEL','QWEN_ALIGNER_MODEL',
                        'DIARIZATION_SEGMENTATION','DIARIZATION_EMBEDDING'):
                if value:=os.getenv(key):
                    definition['EnvironmentVariables'][key]=str(Path(value).expanduser().resolve())
            temporary=self.plist.with_suffix('.tmp')
            with temporary.open('wb') as handle:plistlib.dump(definition,handle)
            os.chmod(temporary,0o600);temporary.replace(self.plist)
            result=self.call('bootstrap',self.domain,str(self.plist))
        if result.returncode:raise RuntimeError('macOS 服务启动失败，请检查当前登录会话和 data/server.log')
        return self.status()

    def busy(self):
        from app.host_bridge import maintenance_busy
        return maintenance_busy(self.data)

    def stop(self,force=False,*,expected_pid=None):
        status=self.status()
        if expected_pid is not None and (status.get('pid') != expected_pid or not status.get('ready')):
            raise RestartBlocked(['service_unverified'])
        if not status['loaded']:return status
        if not force and expected_pid is not None:
            from app.host_bridge import maintenance_report
            blockers = maintenance_report(self.data)['blockers']
            if blockers:raise RestartBlocked(blockers)
        elif not force and self.busy():raise ValueError('有录音或后台任务，请先完成；stop --force 仅强制退出 API，独立采集可能继续，请先在工作台结束录音')
        if status.get('backend')=='detached':
            # A recycled PID is never sufficient authorization to stop a process.
            if not status['ready']:raise ValueError('无法核对服务实例身份，未向记录的 PID 发送停止信号')
            os.kill(status['pid'],signal.SIGTERM)
            return {'stopping':True,'pid':status['pid'],'backend':'detached'}
        result=self.call('bootout',self.target)
        if result.returncode:raise RuntimeError('macOS 服务停止尚未确认，请重新检查状态')
        return self.status()


    def restart(self, expected_pid=None, timeout=30):
        from app.host_bridge import maintenance_report
        status = self.status()
        old_pid = status.get('pid')
        backend = status.get('backend')
        if not status.get('ready') or backend not in ('detached', 'launchd') or (
            expected_pid is not None and old_pid != expected_pid
        ):
            raise RestartBlocked(['service_unverified'])
        if not (self.root / '.venv/bin/python').is_file():
            raise ValueError('请先完成本机 Python 环境安装')
        report = maintenance_report(self.data)
        if report['blockers']:
            raise RestartBlocked(report['blockers'])
        self.stop(expected_pid=old_pid)
        deadline = time.monotonic() + timeout
        while self.occupied() or self.status().get('loaded'):
            if time.monotonic() >= deadline:
                raise RestartBlocked(['stop_timeout'])
            time.sleep(.2)
        started = self.start(detached=backend == 'detached')
        deadline = time.monotonic() + timeout
        while True:
            current = self.status()
            if current.get('ready') and current.get('backend') == backend and current.get('pid') != old_pid:
                if backend == 'detached' and current.get('pid') != started.get('pid'):
                    raise RestartBlocked(['service_unverified'])
                return {**current, 'previous_pid': old_pid, 'recoverable': report['recoverable']}
            if time.monotonic() >= deadline:
                raise RestartBlocked(['start_timeout'])
            time.sleep(.2)


def main():
    parser=argparse.ArgumentParser(description='听记本机服务管理（不设置开机启动）')
    parser.add_argument('action',choices=['start','stop','restart','status'],nargs='?',default='start')
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--expected-pid', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--json-errors', action='store_true', help=argparse.SUPPRESS)
    backend=parser.add_mutually_exclusive_group()
    backend.add_argument('--detached',action='store_true',help='独立进程会话启动（默认），无自动重启')
    backend.add_argument('--launchd',action='store_true',help='可选：当前登录会话由launchd管理；需系统允许访问项目目录')
    args=parser.parse_args()
    if platform.system()!='Darwin':parser.error('此管理入口仅用于 macOS；其他系统可使用 start.command 或 uvicorn')
    if args.force and args.action != 'stop':parser.error('--force 仅用于 stop')
    try:
        service = MacService()
        if args.action == 'status':
            value = service.status()
        else:
            import fcntl
            service.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (service.folder/'operation.lock').open('a') as lock:
                try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:raise ValueError('已有后台管理操作正在执行，请稍候') from None
                if args.action == 'restart':
                    value = service.restart(expected_pid=args.expected_pid)
                elif args.action == 'stop':
                    value = service.stop(args.force)
                else:
                    value = service.start(not args.launchd)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        if args.json_errors:
            print(json.dumps({'blockers': error.reasons if isinstance(error, RestartBlocked) else []}))
            raise SystemExit(1)
        parser.exit(1, str(error)+'\n')

if __name__=='__main__':main()
