"""Bounded recording leases for macOS idle display/system sleep assertions."""
import os,platform,subprocess,threading,time
from pathlib import Path

class KeepAwake:
    def __init__(self,is_open=lambda _:True,native_alive=lambda _:False,ttl=180):
        self.is_open=is_open;self.native_alive=native_alive;self.ttl=ttl
        self.supported=platform.system()=='Darwin' and Path('/usr/bin/caffeinate').is_file()
        self.leases={};self.process=None;self.error='';self.lock=threading.RLock();self.closed=threading.Event();self.monitor=None
    def _stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        self.process=None
    def _refresh(self):
        if not self.leases:self._stop();self.error='';return
        if self.supported and (self.process is None or self.process.poll() is not None):
            try:
                self.process=subprocess.Popen(['/usr/bin/caffeinate','-di','-w',str(os.getpid())],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.error=''
            except OSError:self.error='本机防休眠启动失败，将尝试浏览器亮屏保护'
    def status(self,rid):
        with self.lock:
            return {'supported':self.supported,'active':rid in self.leases and self.process is not None and self.process.poll() is None,'error':self.error,'lease_seconds':self.ttl}
    def acquire(self,rid):
        with self.lock:
            if self.closed.is_set():raise ValueError('防休眠服务已关闭')
            self.leases[rid]=time.monotonic();self._refresh();return self.status(rid)
    def release(self,rid):
        with self.lock:
            self.leases.pop(rid,None);self._refresh();return self.status(rid)
    def expire(self):
        with self.lock:
            for rid,seen in list(self.leases.items()):
                try:alive=self.is_open(rid) and (self.native_alive(rid) or time.monotonic()-seen<self.ttl)
                except Exception:alive=False
                if not alive:self.leases.pop(rid,None)
            self._refresh()
    def start_monitor(self):
        def loop():
            while not self.closed.wait(5):self.expire()
        self.monitor=threading.Thread(target=loop,daemon=True);self.monitor.start()
    def close(self):
        self.closed.set()
        if self.monitor:self.monitor.join(timeout=6)
        with self.lock:self.leases.clear();self._stop()
