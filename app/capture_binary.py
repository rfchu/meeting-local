"""Shared artifact gate: writer/probe users share; a helper update is exclusive."""
import fcntl,hashlib,os,stat
from pathlib import Path

class BinaryLease:
    def __init__(self,executable,exclusive=False):
        base=Path(os.getenv('TINGJI_CAPTURE_DELIVERY_ROOT',str(Path.home()/'Library/Caches/cn.seeknow.tingji/capture-delivery')))
        if not base.is_absolute():raise ValueError('采集器交付目录必须为绝对路径')
        self.folder=base/hashlib.sha256(str(Path(executable).resolve()).encode()).hexdigest()
        self.path=self.folder/'binary.lock';self.exclusive=exclusive;self.fd=None
    def acquire(self):
        for folder in (self.folder.parent,self.folder):
            folder.mkdir(parents=True,exist_ok=True,mode=0o700);info=folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('采集器交付目录权限不安全')
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('采集器交付锁无法核对')
            fcntl.flock(fd,(fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except OSError:os.close(fd);raise ValueError('采集器正在运行或更新，请先结束录音／试录；未覆盖程序') from None
        except BaseException:os.close(fd);raise
        self.fd=fd;return self
    def close(self):
        if self.fd is not None:os.close(self.fd);self.fd=None
    def __enter__(self):return self.acquire()
    def __exit__(self,*_):self.close()
