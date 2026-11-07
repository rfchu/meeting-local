"""E6 local capture broker: same-user socket, fenced controller lease, durable receipts.
No device APIs here. Only the child helper opens audio; API processes never own its stdin.
"""
import argparse,contextlib,ctypes,fcntl,hashlib,json,os,secrets,socket,stat,subprocess,sys,threading,time,uuid
from pathlib import Path

LIMIT=16384
LEASE_SECONDS=10.0

class BrokerError(ValueError):pass


def token(value):return isinstance(value,str) and len(value)==32 and all(c in '0123456789abcdef' for c in value)


def private(path):
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    s=path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o077:raise BrokerError('采集控制目录权限无法核对')


def atomic(path,value):
    temporary=path.with_name('.'+uuid.uuid4().hex)
    try:
        with open(temporary,'x',opener=lambda p,f:os.open(p,f,0o600)) as stream:
            json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:temporary.unlink(missing_ok=True)


def read_private(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o077 or s.st_size>65536:raise BrokerError('采集控制记录无法核对')
        value=json.loads(os.read(fd,65537))
        if not isinstance(value,dict):raise BrokerError('采集控制记录格式无效')
        return value
    finally:os.close(fd)


def peer_uid(sock):
    if sys.platform=='darwin':
        uid=ctypes.c_uint();gid=ctypes.c_uint()
        if ctypes.CDLL(None,use_errno=True).getpeereid(sock.fileno(),ctypes.byref(uid),ctypes.byref(gid))!=0:raise BrokerError('无法核对本机控制端身份')
        return uid.value
    import struct
    return struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]


def endpoint(root,rid=None,incarnation=None):
    fingerprint=hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()
    base=Path(os.getenv('TINGJI_CAPTURE_CONTROL_ROOT',str(Path.home()/'Library/Caches/cn.seeknow.tingji/control')))
    if not base.is_absolute():raise BrokerError('采集控制目录必须为绝对路径')
    key=hashlib.sha256((fingerprint+':'+rid+':'+incarnation).encode()).hexdigest() if rid is not None and incarnation is not None else fingerprint
    folder=base/key[:24]
    path=folder/'c.sock'
    if len(os.fsencode(path))>100:raise BrokerError('采集控制路径过长')
    return folder,path,fingerprint


def receive(sock):
    data=b''
    while b'\n' not in data:
        piece=sock.recv(min(4096,LIMIT+1-len(data)))
        if not piece or len(data)+len(piece)>LIMIT:raise BrokerError('采集控制消息不完整或过大')
        data+=piece
    if data.split(b'\n',1)[1]:raise BrokerError('一次连接只允许一条控制消息')
    value=json.loads(data[:-1])
    if not isinstance(value,dict):raise BrokerError('控制消息格式无效')
    return value


class Client:
    def __init__(self,root,rid,incarnation):
        self.root=Path(root);self.rid=rid;self.incarnation=incarnation
        if not token(rid) or not token(incarnation):raise BrokerError('采集会话身份无效')
        self.folder,self.path,self.fingerprint=endpoint(root,rid,incarnation)
        self.controller=uuid.uuid4().hex;self.credentials=None;self.sequence=0;self.lock=threading.RLock()
    def request(self,operation,**values):
        for folder in (self.folder.parent,self.folder):
            s=folder.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o077:raise BrokerError('本机端点目录不安全')
        s=self.path.lstat()
        if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o077:raise BrokerError('本机控制端点无法核对')
        message={'version':2,'workspace':self.fingerprint,'recording_id':self.rid,'incarnation':self.incarnation,'operation':operation,**values}
        if self.credentials:message.update(self.credentials)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
            sock.settimeout(20 if operation=='command' else 3)
            sock.connect(str(self.path))
            if peer_uid(sock)!=os.getuid():raise BrokerError('控制服务用户不匹配')
            sock.sendall(json.dumps(message).encode()+b'\n');response=receive(sock)
        if response.get('version')!=2 or response.get('incarnation')!=self.incarnation or response.get('workspace')!=self.fingerprint or response.get('recording_id')!=self.rid:raise BrokerError('采集服务握手不匹配')
        if not response.get('ok'):raise BrokerError(response.get('error','采集控制未确认'))
        return response
    def validate_cache(self,value):
        if value.get('version')!=2 or value.get('workspace')!=self.fingerprint or value.get('recording_id')!=self.rid or value.get('incarnation')!=self.incarnation:raise BrokerError('控制缓存与采集实例不匹配')

    def status(self):
        with self.lock:
            try:value=self.request('status')
            except OSError:
                value=read_private(self.folder/'terminal.json')
                self.validate_cache(value)
            self.validate_cache(value)
            if any(type(value.get(k)) is not bool for k in ('alive','control_valid','can_claim')) or type(value.get('generation')) is not int or not isinstance(value.get('state'),str) or (not value['alive'] and type(value.get('exit_code')) is not int):raise BrokerError('采集状态证据不完整，未确认控制或退出')
            if self.credentials and not value['control_valid']:self.credentials=None
            return value
    def claim(self,generation):
        with self.lock:
            value=self.request('claim',expected_generation=generation,controller_id=self.controller)
            self.credentials={'controller_id':self.controller,'controller_token':value['controller_token'],'generation':value['generation']}
            self.sequence=0;return value
    def command(self,command,request_id):
        with self.lock:
            self.sequence+=1
            try:return self.request('command',command=command,request_id=request_id,sequence=self.sequence)
            except OSError:
                terminal=read_private(self.folder/'terminal.json');self.validate_cache(terminal)
                cached=read_private(self.folder/'receipts.json');self.validate_cache(cached)
                receipt=cached.get('receipts',{}).get(request_id)
                if terminal.get('alive') is not False or not isinstance(receipt,dict) or receipt.get('command')!=command:raise BrokerError('控制服务已退出，没有可核对的同一命令回执')
                return {'ok':True,'receipt':receipt,'replayed':True}
    def release(self):
        with self.lock:
            if self.credentials:
                try:self.request('release')
                finally:self.credentials=None


class Broker:
    def __init__(self,root,rid,incarnation,args,lease_seconds=LEASE_SECONDS,awake_enabled=True,helper_path=None):
        self.build='E7';self.started_at=time.time();self.source_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.root=Path(root).resolve();self.rid=rid;self.incarnation=incarnation;self.args=args
        self.folder,self.path,self.fingerprint=endpoint(self.root,rid,incarnation)
        self.lease_seconds=lease_seconds;self.generation=0;self.controller=None;self.deadline=0;self.sequence=0
        self.receipts={};self.child=None;self.lockfd=None;self.awake=None;self.awake_requested=False;self.awake_enabled=awake_enabled;self.writer_guard=None;self.terminal_written=False;self.binary_guard=None;self.helper_path=helper_path
    def release_writer_guard(self):
        if self.writer_guard is not None:os.close(self.writer_guard);self.writer_guard=None
    def terminal(self):
        code=self.child.poll()
        if code is None:return
        self.release_writer_guard()
        if self.binary_guard:self.binary_guard.close();self.binary_guard=None
        if not self.terminal_written:
            atomic(self.folder/'terminal.json',{'version':2,'workspace':self.fingerprint,'recording_id':self.rid,'incarnation':self.incarnation,'broker_build':self.build,'source_digest':self.source_digest,'started_at':self.started_at,'pending_commands':sum(r.get('outcome')!='confirmed' for r in self.receipts.values()),'exit_reason':None if self.child.poll() is None else 'normal_stop' if self.stopped_confirmed() else 'abnormal_or_unconfirmed','ok':True,'alive':False,'exit_code':code,'state':self.state(),'generation':self.generation,'control_valid':False,'can_claim':False,'awake_active':False})
            self.terminal_written=True

    def sync_awake(self):
        try:
            policy=read_private(self.root/self.rid/'capture-policy.json')
            if policy.get('incarnation')==self.incarnation and type(policy.get('keep_awake')) is bool:
                self.awake_requested=policy['keep_awake']
        except (OSError,ValueError):pass
        wanted=self.awake_enabled and self.awake_requested and self.child is not None and self.child.poll() is None
        if wanted and sys.platform=='darwin' and Path('/usr/bin/caffeinate').is_file():
            if self.awake is None or self.awake.poll() is not None:
                try:self.awake=subprocess.Popen(['/usr/bin/caffeinate','-di','-w',str(os.getpid())],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
                except OSError:self.awake=None
        elif self.awake is not None:
            if self.awake.poll() is None:
                self.awake.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):self.awake.wait(timeout=2)
            self.awake=None

    def valid(self,m):
        return self.controller is not None and time.monotonic()<self.deadline and all(m.get(k)==v for k,v in self.controller.items()) and m.get('generation')==self.generation
    def capture_metadata(self):
        try:
            path=self.root/self.rid/'capture.json'
            if path.is_symlink() or path.stat().st_size>65536:return None
            value=json.loads(path.read_text())
            return value if isinstance(value,dict) and value.get('capture_incarnation')==self.incarnation else None
        except (OSError,ValueError):return None
    def state(self):
        value=self.capture_metadata()
        return value.get('state','unknown') if value else 'starting' if self.child.poll() is None else 'unknown'
    def stopped_confirmed(self):
        value=self.capture_metadata()
        return bool(self.child.poll()==0 and value and value.get('state')=='stopped' and not value.get('error') and isinstance(value.get('diagnostics',{}),dict) and value.get('diagnostics',{}).get('failure_stage') in (None,'none'))
    def reply(self,m):
        base={'version':2,'workspace':self.fingerprint,'recording_id':self.rid,'incarnation':self.incarnation}
        try:
            if m.get('version')!=2 or any(m.get(k)!=base[k] for k in ('workspace','recording_id','incarnation')):raise BrokerError('采集实例握手不匹配')
            op=m.get('operation');alive=self.child.poll() is None;valid=self.valid(m)
            if not alive:self.terminal()
            if op=='status':
                if valid:self.deadline=time.monotonic()+self.lease_seconds
                return base|{'broker_build':self.build,'source_digest':self.source_digest,'started_at':self.started_at,'pending_commands':sum(r.get('outcome')!='confirmed' for r in self.receipts.values()),'exit_reason':None if self.child.poll() is None else 'normal_stop' if self.stopped_confirmed() else 'abnormal_or_unconfirmed','ok':True,'alive':alive,'exit_code':self.child.poll(),'state':self.state(),'generation':self.generation,'control_valid':valid,'awake_active':self.awake is not None and self.awake.poll() is None,'can_claim':self.controller is None or time.monotonic()>=self.deadline}
            if op=='claim':
                if type(m.get('expected_generation')) is not int or m['expected_generation']!=self.generation:raise BrokerError('控制代次已变化，请重新检查')
                if not alive:raise BrokerError('采集器已退出，只能整理已保存音轨')
                if self.controller is not None and time.monotonic()<self.deadline:raise BrokerError('原控制租约仍有效，请稍后明确恢复控制')
                if not token(m.get('controller_id')):raise BrokerError('控制者身份无效')
                self.generation+=1;self.sequence=0
                self.controller={'controller_id':m['controller_id'],'controller_token':secrets.token_hex(16)}
                self.deadline=time.monotonic()+self.lease_seconds
                atomic(self.folder/'controller.json',{'version':2,'recording_id':self.rid,'incarnation':self.incarnation,'generation':self.generation})
                return base|{'ok':True,'generation':self.generation,'controller_token':self.controller['controller_token']}
            if not valid:raise BrokerError('当前控制权无效或已被撤销，未发送指令')
            self.deadline=time.monotonic()+self.lease_seconds
            if op=='release':self.controller=None;return base|{'ok':True}
            if op!='command':raise BrokerError('未知控制操作')
            request_id=m.get('request_id');command=m.get('command');sequence=m.get('sequence')
            if not token(request_id) or command not in ('pause','resume','stop') or type(sequence) is not int:raise BrokerError('控制命令无效')
            if request_id in self.receipts:
                receipt=self.receipts[request_id]
                if receipt['command']!=command:raise BrokerError('命令编号已用于其他操作')
                return base|{'ok':True,'receipt':receipt,'replayed':True}
            if sequence<=self.sequence:raise BrokerError('命令序号已过期')
            self.sequence=sequence
            receipt={'request_id':request_id,'command':command,'generation':self.generation,'sequence':sequence,'outcome':'unconfirmed'}
            self.receipts[request_id]=receipt
            self.receipts=dict(list(self.receipts.items())[-64:])
            atomic(self.folder/'receipts.json',{'version':2,'workspace':self.fingerprint,'recording_id':self.rid,'incarnation':self.incarnation,'receipts':self.receipts})
            if not alive:raise BrokerError('采集器已经退出，未重放命令')
            self.child.stdin.write((command+'\n').encode());self.child.stdin.flush()
            wanted={'pause':'paused','resume':'recording','stop':'stopped'}[command]
            until=time.monotonic()+(15 if command=='stop' else 2)
            while time.monotonic()<until:
                state=self.state()
                if (command=='stop' and self.stopped_confirmed()) or (command!='stop' and state==wanted):
                    receipt['outcome']='confirmed';break
                time.sleep(.03)
            receipt['state']=self.state()
            if self.child.poll() is not None:self.terminal()
            atomic(self.folder/'receipts.json',{'version':2,'workspace':self.fingerprint,'recording_id':self.rid,'incarnation':self.incarnation,'receipts':self.receipts})
            self.deadline=time.monotonic()+self.lease_seconds
            return base|{'ok':True,'receipt':receipt,'replayed':False}
        except (OSError,ValueError,TypeError):return base|{'ok':False,'error':str(sys.exc_info()[1]) if isinstance(sys.exc_info()[1],BrokerError) else '采集控制未确认，未自动重放命令'}
    def run(self):
        from .maintenance import DataLease
        with DataLease(self.root.parent):self._run()

    def _run(self):
        private(self.folder.parent);private(self.folder)
        fd=os.open(self.folder/'broker.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600);self.lockfd=fd
        try:
            s=os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o077:raise BrokerError('控制服务锁不安全')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if self.path.exists() or self.path.is_symlink():
                s=self.path.lstat()
                if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=os.getuid():raise BrokerError('已有端点无法安全清理')
                self.path.unlink()
            workspace=endpoint(self.root)[0];private(workspace)
            guard_fd=os.open(workspace/'writer-broker.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            guard_info=os.fstat(guard_fd)
            if not stat.S_ISREG(guard_info.st_mode) or guard_info.st_uid!=os.getuid() or guard_info.st_mode&0o077:
                os.close(guard_fd);raise BrokerError('工作空间控制锁无法核对')
            until=time.monotonic()+1.5
            while True:
                try:fcntl.flock(guard_fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=until:
                        os.close(guard_fd);raise BrokerError('此工作空间已有采集控制服务，未启动第二个写入者')
                    time.sleep(.03)
            self.writer_guard=guard_fd
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(str(self.path));os.chmod(self.path,0o600);server.listen(8);server.settimeout(.5)
                # The writer's own kernel lease is the final duplicate-device guard.
                launch=self.root/self.rid/'capture-launch.json'
                if launch.exists() or launch.is_symlink():raise BrokerError('此采集实例已经启动过，未重新采集或覆盖音轨')
                atomic(launch,{'version':2,'recording_id':self.rid,'incarnation':self.incarnation})
                logpath=self.root/self.rid/'capture.log'
                if self.helper_path:
                    from .capture_binary import BinaryLease
                    self.binary_guard=BinaryLease(self.helper_path).acquire()
                with logpath.open('ab') as log:self.child=subprocess.Popen(self.args,stdin=subprocess.PIPE,stdout=log,stderr=log,close_fds=True)
                metadata={'version':2,'workspace':self.fingerprint,'recording_id':self.rid,'incarnation':self.incarnation}
                atomic(self.folder/'broker.json',metadata);atomic(workspace/'broker.json',metadata)
                exited_at=None
                while True:
                    self.sync_awake()
                    if self.child.poll() is not None:
                        self.terminal()
                        exited_at=exited_at or time.monotonic()
                        if time.monotonic()-exited_at>30:break
                    try:conn,_=server.accept()
                    except socket.timeout:continue
                    with conn:
                        conn.settimeout(3)
                        try:
                            if peer_uid(conn)!=os.getuid():continue
                            message=receive(conn);response=self.reply(message)
                            conn.sendall(json.dumps(response).encode()+b'\n')
                        except (OSError,ValueError):continue
        finally:
            self.release_writer_guard()
            if self.binary_guard:self.binary_guard.close();self.binary_guard=None
            if self.awake and self.awake.poll() is None:
                self.awake.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):self.awake.wait(timeout=2)
            if self.child and self.child.poll() is None:
                # Broker failure is different from API failure: close the pipe, no PID killing.
                with contextlib.suppress(OSError):self.child.stdin.close()
            # Retain a stale socket after exit; the next verified lock owner may replace it.
            if self.lockfd is not None:
                # A failed flock must not unlink another broker's socket.
                os.close(fd)


class BrokerProcess:
    def __init__(self,client):self.client=client;self.pid=None;self.exit_code_known=False
    @property
    def has_control(self):return self.client.credentials is not None
    def poll(self):
        try:
            status=self.client.status();self.exit_code_known=not status['alive'] and type(status.get('exit_code')) is int
            return None if status['alive'] else status['exit_code']
        except (OSError,ValueError):
            from .capture_ownership import inspect,blocks_publication
            self.exit_code_known=False
            return None if blocks_publication(inspect(self.client.root),self.client.rid) else 1
    def command(self,command,request_id):
        result=self.client.command(command,request_id)
        if not isinstance(result.get('receipt'),dict) or result['receipt'].get('request_id')!=request_id or result['receipt'].get('command')!=command or result['receipt'].get('outcome')!='confirmed':raise BrokerError('采集命令结果待确认，未自动重放')
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True);parser.add_argument('--recording-id',required=True);parser.add_argument('--incarnation',required=True);parser.add_argument('--helper',type=Path,required=True);parser.add_argument('--microphone',action='store_true')
    args=parser.parse_args()
    if not token(args.recording_id) or not token(args.incarnation):parser.error('无效会话身份')
    folder=args.root/args.recording_id
    session=json.loads((folder/'session.json').read_text())
    if session.get('id')!=args.recording_id or session.get('capture_incarnation')!=args.incarnation or session.get('capture_transport')!='broker_v2':parser.error('会话身份不匹配')
    command=[str(args.helper),'--output',str(folder),'--incarnation',args.incarnation]
    if args.microphone:command.append('--microphone')
    Broker(args.root,args.recording_id,args.incarnation,command,helper_path=args.helper).run()

if __name__=='__main__':main()
