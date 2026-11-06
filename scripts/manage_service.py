"""Standalone service, with optional current-login launchd management.

The HTTP/capture process belongs to launchd rather than a development terminal.
"""
import sys,argparse,json,os,platform,plistlib,re,socket,sqlite3,subprocess,time,uuid,signal,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.paths import RuntimePaths
LABEL='cn.seeknow.tingji.local'


class MacService:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.data=RuntimePaths.from_env(root=self.root).data
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
        result=self.call('print',self.target)
        if result.returncode:return {'loaded':False,'running':False,'port':self.port}
        def field(pattern):
            found=re.search(pattern,result.stdout,re.M);return found.group(1) if found else None
        pid=field(r'^\s*pid = (\d+)$');code=field(r'^\s*last exit code = (-?\d+)(?::[^\n]*)?$')
        return {'loaded':True,'running':bool(pid),'ready':bool(pid and self.identity().get('pid')==int(pid)),'backend':'launchd','pid':int(pid) if pid else None,
                'state':field(r'^\s*state = ([^\n]+)$'),'last_exit_code':int(code) if code else None,'port':self.port}

    def identity(self):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/runtime/service',timeout=2) as reply:return json.load(reply)
        except (OSError,ValueError):return {}

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
        if detached:
            if status['loaded']:
                result=self.call('bootout',self.target)
                if result.returncode:raise RuntimeError('旧托管实例尚未卸载，未启动第二个服务')
            self.folder.mkdir(parents=True,exist_ok=True);os.chmod(self.folder,0o700)
            instance=uuid.uuid4().hex;env=os.environ.copy()
            env.update(MEETING_DATA=str(self.data),TINGJI_INSTANCE_ID=instance,TINGJI_SERVICE_MODE='detached',PYTHONUNBUFFERED='1')
            with (self.data/'server.log').open('ab') as log:
                process=subprocess.Popen([str(python),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(self.port),'--timeout-graceful-shutdown','20'],
                    cwd=self.root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,start_new_session=True,close_fds=True)
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
            temporary=self.plist.with_suffix('.tmp')
            with temporary.open('wb') as handle:plistlib.dump(definition,handle)
            os.chmod(temporary,0o600);temporary.replace(self.plist)
            result=self.call('bootstrap',self.domain,str(self.plist))
        if result.returncode:raise RuntimeError('macOS 服务启动失败，请检查当前登录会话和 data/server.log')
        return self.status()

    def busy(self):
        database=self.data/'meetings.sqlite3'
        if database.exists():
            with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as db:
                if db.execute("SELECT 1 FROM jobs WHERE state IN ('running','queued') LIMIT 1").fetchone():return True
        for path in (self.data/'recordings').glob('*/session.json'):
            session=json.loads(path.read_text())
            if session.get('state') in ('recording','paused') and time.time()-path.stat().st_mtime<120:return True
        return False

    def stop(self,force=False):
        status=self.status()
        if not status['loaded']:return status
        if not force and self.busy():raise ValueError('有录音或后台任务，请先完成；stop --force 仅强制退出 API，独立采集可能继续，请先在工作台结束录音')
        if status.get('backend')=='detached':
            # A recycled PID is never sufficient authorization to stop a process.
            if not status['ready']:raise ValueError('无法核对服务实例身份，未向记录的 PID 发送停止信号')
            os.kill(status['pid'],signal.SIGTERM)
            return {'stopping':True,'pid':status['pid'],'backend':'detached'}
        result=self.call('bootout',self.target)
        if result.returncode:raise RuntimeError('macOS 服务停止尚未确认，请重新检查状态')
        return self.status()


def main():
    parser=argparse.ArgumentParser(description='听记本机服务管理（不设置开机启动）')
    parser.add_argument('action',choices=['start','stop','status'],nargs='?',default='start')
    parser.add_argument('--force',action='store_true')
    backend=parser.add_mutually_exclusive_group()
    backend.add_argument('--detached',action='store_true',help='独立进程会话启动（默认），无自动重启')
    backend.add_argument('--launchd',action='store_true',help='可选：当前登录会话由launchd管理；需系统允许访问项目目录')
    args=parser.parse_args()
    if platform.system()!='Darwin':parser.error('此管理入口仅用于 macOS；其他系统可使用 start.command 或 uvicorn')
    try:
        service=MacService();value=service.stop(args.force) if args.action=='stop' else service.start(not args.launchd) if args.action=='start' else service.status()
        print(json.dumps(value,ensure_ascii=False,indent=2))
    except (OSError,ValueError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')

if __name__=='__main__':main()
