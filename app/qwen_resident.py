"""Lazy resident Qwen process with cancellation, idle expiry and model identity."""
import asyncio,json,time
from pathlib import Path
import anyio
from .db import uid

class QwenResident:
    def __init__(self,root,idle_seconds=180):
        self.root=Path(root);self.idle_seconds=idle_seconds;self.lock=asyncio.Lock()
        self.process=None;self.log=None;self.identity=None;self.ready=False;self.busy=False
        self.loads=0;self.requests=0;self.last_used=time.monotonic();self.peak_memory_gb=0;self.load_ms=0
        self.model='';self.monitor=None
    def status(self):
        alive=self.process is not None and self.process.returncode is None
        return {'loaded':alive and self.ready,'loading':alive and not self.ready,'busy':self.busy,
                'model':self.model,'load_count':self.loads,'requests':self.requests,
                'idle_seconds':round(time.monotonic()-self.last_used),'idle_timeout_seconds':self.idle_seconds,
                'peak_memory_gb':round(self.peak_memory_gb,3),'load_ms':self.load_ms}
    async def _stop(self):
        process=self.process;self.ready=False
        if process and process.returncode is None:
            try:process.terminate()
            except ProcessLookupError:pass
            try:await asyncio.wait_for(process.wait(),3)
            except asyncio.TimeoutError:
                try:process.kill()
                except ProcessLookupError:pass
                await process.wait()
        self.process=None;self.identity=None
        if self.log:self.log.close();self.log=None
    async def close(self):
        async with self.lock:
            with anyio.CancelScope(shield=True):await self._stop()
    async def shutdown(self):
        if self.monitor:
            self.monitor.cancel()
            try:await self.monitor
            except asyncio.CancelledError:pass
            self.monitor=None
        await self.close()
    async def expire_idle(self):
        if self.status()['loaded'] and not self.busy and time.monotonic()-self.last_used>=self.idle_seconds:
            async with self.lock:
                if not self.busy and time.monotonic()-self.last_used>=self.idle_seconds:await self._stop()
    def start_monitor(self):
        async def loop():
            while True:
                await asyncio.sleep(10)
                await self.expire_idle()
        self.monitor=asyncio.create_task(loop())
    async def _ensure(self,provider):
        identity=provider.identity()
        if self.status()['loaded'] and identity==self.identity:return
        await self._stop()
        if not provider.ready():raise RuntimeError('Qwen 模型尚未就绪')
        self.model=Path(provider.model).name;self.log=(self.root/'qwen-resident.log').open('ab')
        try:
            with anyio.CancelScope(shield=True):
                self.process=await asyncio.create_subprocess_exec(*provider.resident_command(),
                    stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=self.log,limit=256*1024)
            async with asyncio.timeout(90):
                while line:=await self.process.stdout.readline():
                    try:event=json.loads(line)
                    except (ValueError,UnicodeDecodeError):continue
                    if event.get('type')=='ready':
                        self.ready=True;self.identity=identity;self.loads+=1
                        self.load_ms=event.get('load_ms',0);self.peak_memory_gb=event.get('peak_memory_gb',event.get('memory_gb',0))
                        self.last_used=time.monotonic();return
                raise RuntimeError('常驻 Qwen 启动失败，请检查本机模型或切换整批模式')
        except BaseException:
            with anyio.CancelScope(shield=True):await self._stop()
            raise
    async def warm(self,provider):
        async with self.lock:
            self.busy=True
            try:await self._ensure(provider)
            finally:self.busy=False;self.last_used=time.monotonic()
            return self.status()
    async def generate(self,provider,audio):
        async with self.lock:
            self.busy=True;completed=False;rid=uid()
            try:
                await self._ensure(provider)
                self.process.stdin.write((json.dumps({'id':rid,'audio':str(audio)})+'\n').encode());await self.process.stdin.drain()
                async with asyncio.timeout(90):
                    while line:=await self.process.stdout.readline():
                        try:event=json.loads(line)
                        except (ValueError,UnicodeDecodeError):continue
                        if event.get('id')!=rid:raise RuntimeError('常驻模型返回的请求标识不一致')
                        if event.get('type')=='qwen_error':raise RuntimeError(event.get('message') or '常驻识别失败')
                        if event.get('type') not in ('qwen_partial','qwen_final'):raise RuntimeError('常驻模型返回了未知事件')
                        if event['type']=='qwen_final':
                            self.peak_memory_gb=event.get('peak_memory_gb',self.peak_memory_gb);self.requests+=1
                            completed=True
                        yield event
                        if completed:return
                    raise RuntimeError('常驻识别进程提前退出，临时结果未确认')
            finally:
                try:
                    if not completed:
                        with anyio.CancelScope(shield=True):await self._stop()
                finally:self.busy=False;self.last_used=time.monotonic()
