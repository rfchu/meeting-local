"""Control a local ScreenCaptureKit helper; preserve independent PCM tracks."""
import io, json, math, os, platform, subprocess, threading, time, wave
from array import array
from pathlib import Path
from .recordings import RecordingStore
from .capture_diagnostics import sanitize
from .live_segmentation import elastic_plan,HARD_SECONDS

BASE=Path(__file__).resolve().parents[1]
HELPER=Path(os.getenv('TINGJI_CAPTURE_APP',str(Path.home()/'Library/Caches/cn.seeknow.tingji/TingjiCapture.app')))/'Contents/MacOS/TingjiCapture'

class NativeCapture:
    def __init__(self,store,recordings):
        self.store=store;self.recordings=recordings;self.processes={};self.last_seen={}
        self.stop_reasons={};self.detached=set();self.lock=threading.RLock();self.closed=threading.Event();self.monitor=None

    def is_running(self,rid):
        with self.lock:
            process=self.processes.get(rid)
            return process is not None and process.poll() is None

    def exit_code(self,rid):
        """Read an owned process exit without persisting events or renewing the page lease."""
        with self.lock:
            process=self.processes.get(rid)
            return process.poll() if process is not None else None

    def available(self):return platform.system()=='Darwin' and HELPER.is_file()

    def capability(self):
        result={'available':self.available(),'screen_permission':False,'microphone_permission':False,
                'supported':platform.system()=='Darwin','build_command':'bash scripts/build_capture.sh'}
        if self.available():
            try:
                probe=subprocess.run([str(HELPER),'--probe'],capture_output=True,timeout=5,check=True)
                result.update(json.loads(probe.stdout))
            except (subprocess.SubprocessError,ValueError):result.update(available=False,error='采集器检查失败，请重新构建')
        return result

    def start_monitor(self):
        def loop():
            while not self.closed.wait(5):
                self.stop_abandoned()
        self.monitor=threading.Thread(target=loop,daemon=True);self.monitor.start()

    def reconcile_interrupted(self):
        """A new backend never claims an old native stream is still being captured."""
        with self.lock,self.recordings.lock:
            for path in self.recordings.root.glob('*/session.json'):
                try:
                    session=json.loads(path.read_text());rid=session['id']
                    if session.get('source')!='macos' or session.get('state')=='completed' or self.is_running(rid):continue
                    if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):continue
                    if session.get('state') in ('recording','paused','starting'):
                        session.update(state='interrupted',interrupted_at=time.time())
                        session.setdefault('stop_reason','服务恢复时无法接管原采集进程，录音未重新开始；已保存音轨可恢复')
                        RecordingStore.write(path,session)
                        self.recordings.event('capture.reconciled',rid,duration_seconds=session.get('duration',0))
                except (OSError,ValueError,KeyError):continue

    def stop_abandoned(self):
        # Native capture belongs to the service, not a browser polling lease.
        # Background throttling or a closed tab must never silently end audio.
        with self.lock:
            for rid in list(self.processes):
                if self.processes[rid].poll() is not None:
                    self.observe_exit(rid)
                    continue
                if time.monotonic()-self.last_seen.get(rid,0)>300 and rid not in self.detached:
                    self.detached.add(rid)
                    self.recordings.event('page.detached',rid)

    def record_stop_reason(self,rid,reason):
        self.stop_reasons[rid]=reason
        with self.recordings.lock:
            session=self.recordings.read(rid);session['stop_reason']=reason
            RecordingStore.write(self.recordings.folder(rid)/'session.json',session)

    def observe_exit(self,rid):
        """Persist an observed exit once. Never infer OOM/sleep from a signal alone."""
        with self.lock:
            process=self.processes.get(rid)
            code=process.poll() if process else None
            if code is None:return None
            try:path=self.recordings.folder(rid)/'capture-exit.json'
            except OSError:return code
            if not path.exists():
                try:RecordingStore.write(path,{'exit_code':code,'observed_at':time.time()})
                except OSError:pass # A full disk must not hide the known process exit.
                self.recordings.event('capture.exited',rid,exit_code=code)
            return code

    def start(self,microphone=True):
        with self.lock:
            if not self.available():raise ValueError('原生采集器未安装，请运行 bash scripts/build_capture.sh')
            if any(p.poll() is None for p in self.processes.values()):raise ValueError('已有系统音频采集正在运行，请先停止')
            session=self.recordings.create('audio/wav');rid=session['id']
            session.update(source='macos',include_microphone=microphone)
            folder=self.recordings.folder(rid);RecordingStore.write(folder/'session.json',session)
            args=[str(HELPER),'--output',str(folder)]
            if microphone:args.append('--microphone')
            with (folder/'capture.log').open('ab') as log:
                process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=log,stderr=log)
            self.processes[rid]=process;self.last_seen[rid]=time.monotonic()
            self.recordings.event('capture.started',rid,pid=getattr(process,'pid',None))
            return session

    def status(self,rid,touch=True):
        session=self.recordings.read(rid)
        if session.get('source')!='macos':raise ValueError('不是原生录音会话')
        if touch:
            with self.lock:
                self.last_seen[rid]=time.monotonic()
                if rid in self.detached:
                    self.detached.discard(rid)
                    self.recordings.event('page.returned',rid)
        path=self.recordings.folder(rid)/'capture.json'
        try:status=json.loads(path.read_text())
        except (FileNotFoundError,ValueError):status={'state':'starting','duration':0,'error':''}
        process=self.processes.get(rid)
        code=self.observe_exit(rid) if process else None
        if (not process or code is not None) and status['state'] in ('starting','recording','paused'):
            detail=f'（退出码 {code}）' if code is not None else ''
            status.update(state='interrupted',error=f'采集进程已退出{detail}，已保存的音轨可恢复')
        reason=session.get('stop_reason') or self.stop_reasons.get(rid)
        if reason:status['error']=status.get('error') or reason
        # Disk heartbeat is independent of browser polling. Do not mistake silence for a crash.
        status['diagnostics']=sanitize(status.get('diagnostics'))
        status['health']=self.health(session,status,path)
        return status

    @staticmethod
    def health(session,status,path):
        state=status['state'];duration=status.get('duration',0)
        heartbeat_age=max(0,time.time()-path.stat().st_mtime) if path.exists() else None
        stalled=state=='recording' and heartbeat_age is not None and heartbeat_age>10
        tracks=[]
        for name in ('system','microphone') if session.get('include_microphone') else ('system',):
            frames=status.get(name+'_frames',0)
            age=status.get('buffer_age',{}).get(name)
            silence=status.get('silence_seconds',{}).get(name)
            if state=='paused':signal='paused'
            elif state!='recording':signal='inactive'
            elif stalled:signal='stalled'
            elif duration>=10 and (not frames or (age is not None and age>=10)):signal='no_data'
            elif silence is not None and silence>=10:signal='quiet'
            elif frames:signal='receiving'
            else:signal='waiting'
            # Callback counters are not evidence that the corresponding PCM file grew.
            try:
                info=(path.parent/(name+'.pcm')).stat()
                saved_frames=info.st_size//2;write_age=max(0,time.time()-info.st_mtime)
            except FileNotFoundError:saved_frames=0;write_age=None
            if state=='paused':file_state='paused'
            elif state!='recording':file_state='inactive'
            elif stalled:file_state='unknown'
            elif signal=='no_data':file_state='available' if saved_frames else 'waiting'
            elif duration>=10 and age is not None and age<10 and frames and (
                write_age is None or write_age>=10 or frames-saved_frames>32000
            ):file_state='unconfirmed'
            else:file_state='available' if signal=='quiet' else 'writing' if saved_frames else 'waiting'
            tracks.append({'name':name,'state':signal,'saved_seconds':round(saved_frames/16000,1),
                           'reported_seconds':round(frames/16000,1),'file_state':file_state,
                           'file_age_seconds':round(write_age,1) if write_age is not None else None})
        return {'stalled':stalled,'heartbeat_age_seconds':round(heartbeat_age,1) if heartbeat_age is not None else None,'tracks':tracks}

    def draft_window(self,rid,offset,window_seconds=15,responsive=False,allow_stopped=False,sentence_end=None):
        """Read a bounded snapshot from original PCM; never advance shared cursors."""
        if not math.isfinite(offset) or offset<0 or offset>86400:raise ValueError('草稿时间无效')
        with self.lock:
            session=self.recordings.read(rid)
            if session.get('source')!='macos':raise ValueError('不是原生录音会话')
            if session['state']=='completed':raise ValueError('录音已完成，请查看正式文字稿')
            status=self.status(rid,touch=not allow_stopped)
            if status['state'] not in (('recording','paused','stopped','interrupted','failed') if allow_stopped else ('recording','paused')):raise ValueError('采集尚未就绪或已经停止')
            # Reported frame positions are the audio timeline, not a wall clock estimate.
            available_frames=max(status.get('system_frames',0),status.get('microphone_frames',0))
            horizon=min(status.get('duration',0),available_frames/16000)
            begin=round(offset*16000);end=int(horizon*16000);skipped=0
            elastic=responsive and allow_stopped
            limit=HARD_SECONDS if elastic else 15
            if end-begin>2*limit*16000:
                latest=max(begin,end-limit*16000);skipped=(latest-begin)/16000;begin=latest
            if skipped:sentence_end=None
            names=('system','microphone') if session.get('include_microphone') else ('system',)
            quiet=responsive and all(status.get('silence_seconds',{}).get(name,0)>=.8 for name in names)
            if quiet and available_frames>0:
                horizon=status.get('duration',horizon);end=round(horizon*16000)
            confirmed=not responsive or status['state'] in ('paused','stopped','interrupted','failed') or quiet or end-begin>=15*16000
            maximum=limit if elastic else (15 if confirmed else window_seconds)
            count=min(maximum*16000,max(0,end-begin))
            minimum=8000 if status['state'] in ('paused','stopped','interrupted','failed') else (3 if quiet else min(window_seconds,limit) if elastic else maximum)*16000
            if elastic and sentence_end is not None and status['state']=='recording':minimum=3*16000
            if count<minimum:return {'ready':False,'next_offset':offset,'skipped_seconds':0}
            folder=self.recordings.folder(rid);tracks=[]
            for name in ('system','microphone') if session.get('include_microphone') else ('system',):
                path=folder/(name+'.pcm')
                samples=array('h')
                if path.exists():
                    with path.open('rb') as stream:
                        stream.seek(begin*2);raw=stream.read(count*2);samples.frombytes(raw[:len(raw)//2*2])
                samples.extend([0]*(count-len(samples)));tracks.append(samples)
            mixed=tracks[0] if len(tracks)==1 else array('h',(max(-32768,min(32767,a+b)) for a,b in zip(*tracks)))
            boundary={}
            if elastic:
                forced='pause' if status['state']=='paused' else 'stop' if status['state'] in ('stopped','interrupted','failed') else None
                plan=elastic_plan(mixed.tobytes(),window_seconds,forced,sentence_end)
                if plan is None:return {'ready':False,'next_offset':offset,'skipped_seconds':0}
                count=plan.pop('count');confirmed=plan['confirmed'];mixed=mixed[:count];boundary=plan
            output=io.BytesIO()
            with wave.open(output,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(mixed.tobytes())
            return {'ready':True,'offset':begin/16000,'next_offset':(begin+count)/16000,
                    'skipped_seconds':skipped,'silent':not any(mixed),'payload':output.getvalue(),'confirmed':confirmed,**boundary}

    def command(self,rid,command):
        with self.lock:
            if command=='stop':self.recordings.event('capture.stop_requested',rid)
            self.status(rid);process=self.processes.get(rid)
            if not process or process.poll() is not None:
                if command=='stop':return self.status(rid)
                raise ValueError('采集器已停止')
            try:process.stdin.write((command+'\n').encode());process.stdin.flush()
            except (BrokenPipeError,OSError):
                if command!='stop':raise ValueError('无法控制采集器，请恢复已有录音')
            if command=='stop':
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                process.stdin.close()
                if self.status(rid)['state'] in ('starting','recording','paused','interrupted'):
                    if rid not in self.stop_reasons:self.record_stop_reason(rid,'采集进程未正常确认停止，已保存的音轨可恢复')
                self.processes.pop(rid,None);self.last_seen.pop(rid,None)
            else:
                wanted='paused' if command=='pause' else 'recording'
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    state=self.status(rid)
                    if state['state']==wanted:
                        self.recordings.event('capture.paused' if command=='pause' else 'capture.resumed',rid,duration_seconds=state['duration'])
                        return state
                    if state['state'] in ('failed','interrupted','stopped'):raise ValueError(state.get('error') or '采集已中断')
                    time.sleep(.05)
                raise ValueError('采集控制尚未确认，请检查状态后重试')
            return self.status(rid)

    def finish(self,rid):
        with self.lock:
            if self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):return rid
            status=self.command(rid,'stop');folder=self.recordings.folder(rid)
            session=self.recordings.read(rid)
            if session['state']=='completed':raise ValueError('此录音已完成并被删除，不能重新恢复')
            sources=[folder/f'{name}.pcm' for name in ('system','microphone')]
            sources=[path for path in sources if path.is_file() and path.stat().st_size>0]
            if not sources:raise ValueError(status.get('error') or '尚未采集到音频，请授权并开始播放会议声音')
            # ScreenCaptureKit may stop audio callbacks during silence; retain the clock tail.
            timeline=max(0,float(status.get('duration',0)))
            if not math.isfinite(timeline) or timeline>62500:raise ValueError('录音时间线无效，原始音轨已保留')
            minimum_bytes=round(timeline*16000)*2
            for source in sources:
                if source.stat().st_size<minimum_bytes:
                    with source.open('r+b') as stream:stream.truncate(minimum_bytes);stream.flush();os.fsync(stream.fileno())
            target=self.store.directory(rid);target.mkdir(exist_ok=True)
            args=['ffmpeg','-v','error','-y']
            for source in sources:args+=['-f','s16le','-ar','16000','-ac','1','-i',str(source)]
            if len(sources)>1:args+=['-filter_complex','amix=inputs=2:duration=longest:normalize=0,alimiter=level=false']
            args+=['-c:a','pcm_s16le',str(target/'mix.tmp.wav')]
            result=subprocess.run(args,capture_output=True,timeout=180)
            if result.returncode:raise RuntimeError('音轨合成失败，原始音轨已保留：'+result.stderr.decode(errors='replace')[-600:])
            (target/'mix.tmp.wav').replace(target/'original')
            with (target/'original').open('rb') as stream:os.fsync(stream.fileno())
            tracks=[]
            for source in sources:
                output=target/(source.stem+'.wav')
                with wave.open(str(output),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                    with source.open('rb') as stream:
                        while block:=stream.read(1024*1024):wav.writeframesraw(block)
                with output.open('rb') as stream:os.fsync(stream.fileno())
                tracks.append(output.name)
            with wave.open(str(target/'original'),'rb') as wav:duration=wav.getnframes()/wav.getframerate()
            session=self.recordings.read(rid);filename='线上会议-'+time.strftime('%Y%m%d-%H%M%S',time.localtime(session['created']))+'.wav'
            RecordingStore.write(target/'asset.json',{'filename':filename,'source':'macos','tracks':tracks,'sample_rate':16000})
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT id FROM meetings WHERE id=?',(rid,)).fetchone():
                    db.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(rid,Path(filename).stem,filename,duration))
                    from .meeting_flow import publish_preparation
                    publish_preparation(db,rid,session.get('brief'))
                    from .db import uid
                    for note in session['notes']:
                        from .notes import insert_note
                        insert_note(db,rid,note)
            session.update(state='completed',duration=duration);RecordingStore.write(folder/'session.json',session)
            self.recordings.event('storage.completed',rid,duration_seconds=duration,saved_bytes=(target/'original').stat().st_size)
            for source in sources:source.unlink(missing_ok=True)
            return rid

    def close(self):
        self.closed.set()
        for rid in list(self.processes):
            try:
                self.record_stop_reason(rid,'服务退出，已停止采集；可以恢复已保存的录音')
                self.command(rid,'stop')
            except (ValueError,RuntimeError):pass
        if self.monitor:self.monitor.join(timeout=6)
