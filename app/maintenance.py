"""Non-blocking shared runtime / exclusive offline-maintenance barrier."""
import fcntl,os
from pathlib import Path

class DataLease:
    def __init__(self,root,exclusive=False):
        self.root=Path(root).resolve();self.exclusive=exclusive;self.handle=None
    def acquire(self):
        self.root.mkdir(parents=True,exist_ok=True)
        if not self.exclusive:
            if (self.root/'.restore-in-progress').exists():raise RuntimeError('恢复尚未完成，未启动应用，请先核对恢复结果')
            if (self.root/'.backup-payload').exists():raise RuntimeError('备份载荷不可作为运行目录，请先恢复到新目录')
        path=self.root/'maintenance.lock'
        fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        self.handle=os.fdopen(fd,'a')
        try:fcntl.flock(self.handle, (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except OSError:
            self.close();raise RuntimeError('数据目录正在使用或维护中，未执行操作') from None
        # The marker can appear just before the lock becomes available.
        if not self.exclusive and ((self.root/'.restore-in-progress').exists() or (self.root/'.backup-payload').exists()):
            self.close();raise RuntimeError('数据目录尚未就绪，未启动应用')
        return self
    def close(self):
        if self.handle:self.handle.close();self.handle=None
    def __enter__(self):return self.acquire()
    def __exit__(self,*_):self.close()
