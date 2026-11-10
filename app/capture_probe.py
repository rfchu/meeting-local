"""Short native audio checks isolated from durable meeting recordings."""
import io,json,math,subprocess,tempfile,threading,time,wave
from array import array
from pathlib import Path
from .db import uid
from .native_capture import HELPER
from .recordings import RecordingStore
from .capture_binary import BinaryLease

class CaptureProbe:
    def __init__(self,native):
        self.native=native;self.lock=threading.RLock();self.items={};self.closed=threading.Event();self.thread=None;self.process_factory=None

    def active(self):return any(item['state'] in ('starting','recording') for item in self.items.values())

    def start(self,source):
        with self.lock,self.native.lock:
            self.prune()
            if self.native.ownership()['lease']!='free' or self.active() or any(p.poll() is None for p in self.native.processes.values()):raise ValueError('正在采集声音，请先结束当前录音或测试')
            if not self.native.available():raise ValueError('原生采集器尚未就绪，请先完成采集配置')
            temporary=tempfile.TemporaryDirectory(prefix='tingji-audio-check-');key=uid();incarnation=uid();folder=Path(temporary.name)/key;folder.mkdir()
            RecordingStore.write(folder/'session.json',{'id':key,'capture_incarnation':incarnation,'source':'macos','state':'starting'})
            log=(folder/'capture.log').open('ab');args=[str(HELPER),'--output',str(folder),'--incarnation',incarnation]
            if source=='macos-microphone':args.append('--microphone-only')
            elif source=='macos-both':args.append('--microphone')
            gate=BinaryLease(HELPER)
            try:
                gate.acquire()
                process=(self.process_factory or subprocess.Popen)(args,stdin=subprocess.PIPE,stdout=log,stderr=log)
            except BaseException:gate.close();log.close();temporary.cleanup();raise
            self.items[key]={'id':key,'state':'starting','source':source,'duration':0,'error':'',
                'created':time.monotonic(),'finished':None,'temporary':temporary,'folder':folder,'process':process,'log':log,'binary_guard':gate}
            return self.public(self.items[key])

    @staticmethod
    def public(item):
        return {key:item[key] for key in ('id','state','source','duration','error')} | {'levels':item.get('levels', {})}

    def finish(self,item):
        process=item['process']
        if process.poll() is None:
            try:process.stdin.write(b'stop\n');process.stdin.flush();process.wait(timeout=3)
            except (OSError,subprocess.TimeoutExpired):
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        if process.stdin:process.stdin.close()
        if item.get('binary_guard'):item['binary_guard'].close()
        item['log'].close();item['finished']=time.monotonic()
        if item['state']!='failed':item['state']='completed'
        item['levels'] = item.get('peaks', {})

    def tick(self):
        with self.lock:
            for item in self.items.values():
                if item['state'] not in ('starting','recording'):continue
                path=item['folder']/'capture.json'
                if path.exists():
                    try:
                        status=json.loads(path.read_text());item['duration']=min(10,float(status.get('duration',0)))
                        if status.get('state')=='recording':item['state']='recording'
                        levels = item.setdefault('levels', {})
                        peaks = item.setdefault('peaks', {})
                        for track in ('system', 'microphone'):
                            samples = status.get(track + '_levels', [])
                            if isinstance(samples, list) and samples:
                                peak = max((v for v in samples[-72:] if type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1), default=0)
                                levels[track] = peak
                                peaks[track] = max(peaks.get(track, 0), peak)

                        if status.get('error') or status.get('state') in ('failed','interrupted'):
                            item.update(state='failed',error=str(status.get('error') or '测试输入已中断')[:500])
                        if status.get('duration',0)>=10 or status.get('state') in ('failed','interrupted','stopped'):self.finish(item);continue
                    except (ValueError,OSError):pass
                if item['process'].poll() is not None:
                    item.update(state='failed',error=item['error'] or '测试采集未完成，请检查系统录音权限');self.finish(item)
                elif time.monotonic()-item['created']>45:
                    item.update(state='failed',error='等待采集超时，请检查权限后重试');self.finish(item)
            self.prune()

    def prune(self):
        for key,item in list(self.items.items()):
            if item['finished'] is not None and time.monotonic()-item['finished']>300:
                item['temporary'].cleanup();del self.items[key]

    def read(self,key):
        self.tick()
        with self.lock:return self.public(self.items[key])

    def audio(self,key):
        self.tick()
        with self.lock:
            item=self.items[key]
            if item['state']!='completed':raise ValueError('请等待十秒试录完成')
            tracks=[]
            source_tracks={'macos-both':('system','microphone'),'macos-microphone':('microphone',),'macos-system':('system',)}
            for name in source_tracks[item['source']]:
                path=item['folder']/(name+'.pcm')
                if path.exists():
                    raw=path.read_bytes()[:320000];samples=array('h');samples.frombytes(raw[:len(raw)//2*2]);tracks.append(samples)
            count=max((len(t) for t in tracks),default=0)
            if not count:raise ValueError('未收到测试音频，请检查音源和系统权限')
            mixed=array('h',(max(-32768,min(32767,sum(t[i] for t in tracks if i<len(t)))) for i in range(count)))
            output=io.BytesIO()
            with wave.open(output,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(mixed.tobytes())
            return output.getvalue()

    def delete(self,key):
        with self.lock:
            item=self.items.pop(key,None)
            if not item:return
            self.finish(item);item['temporary'].cleanup()

    def start_monitor(self):
        def monitor():
            while not self.closed.wait(.2):self.tick()
        self.thread=threading.Thread(target=monitor,daemon=True);self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(timeout=1)
        with self.lock:
            for key in list(self.items):self.delete(key)
