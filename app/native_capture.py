"""Control a local ScreenCaptureKit helper; preserve independent PCM tracks."""
import json, os, platform, subprocess, threading, time, wave
from pathlib import Path
from .recordings import RecordingStore

BASE=Path(__file__).resolve().parents[1]
HELPER=Path(os.getenv('TINGJI_CAPTURE_APP',str(Path.home()/'Library/Caches/cn.seeknow.tingji/TingjiCapture.app')))/'Contents/MacOS/TingjiCapture'

class NativeCapture:
    def __init__(self,store,recordings):
        self.store=store;self.recordings=recordings;self.processes={};self.last_seen={}
        self.stop_reasons={};self.lock=threading.RLock();self.closed=threading.Event();self.monitor=None

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

    def stop_abandoned(self):
        # Background browser tabs may throttle their heartbeat to a minute.
        with self.lock:
            for rid in list(self.processes):
                if time.monotonic()-self.last_seen.get(rid,0)>300:
                    try:
                        self.record_stop_reason(rid,'页面失联超过 5 分钟，已自动停止采集；可以恢复已保存的录音')
                        self.command(rid,'stop')
                    except (ValueError,RuntimeError):pass

    def record_stop_reason(self,rid,reason):
        self.stop_reasons[rid]=reason
        with self.recordings.lock:
            session=self.recordings.read(rid);session['stop_reason']=reason
            RecordingStore.write(self.recordings.folder(rid)/'session.json',session)

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
            self.processes[rid]=process;self.last_seen[rid]=time.monotonic();return session

    def status(self,rid):
        session=self.recordings.read(rid)
        if session.get('source')!='macos':raise ValueError('不是原生录音会话')
        self.last_seen[rid]=time.monotonic();path=self.recordings.folder(rid)/'capture.json'
        try:status=json.loads(path.read_text())
        except (FileNotFoundError,ValueError):status={'state':'starting','duration':0,'error':''}
        process=self.processes.get(rid)
        if (not process or process.poll() is not None) and status['state'] in ('starting','recording','paused'):
            status.update(state='interrupted',error='采集进程已退出，已保存的音轨可恢复')
        reason=session.get('stop_reason') or self.stop_reasons.get(rid)
        if reason:status['error']=status.get('error') or reason
        # Disk heartbeat is independent of browser polling. Do not mistake silence for a crash.
        status['health']=self.health(session,status,path)
        return status

    @staticmethod
    def health(session,status,path):
        state=status['state'];duration=status.get('duration',0)
        stalled=state=='recording' and path.exists() and time.time()-path.stat().st_mtime>10
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
            tracks.append({'name':name,'state':signal,'saved_seconds':round(frames/16000,1)})
        return {'stalled':stalled,'tracks':tracks}

    def command(self,rid,command):
        with self.lock:
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
                    if state['state']==wanted:return state
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
                    from .db import uid
                    for note in session['notes']:
                        db.execute('INSERT INTO meeting_notes(id,meeting_id,captured_at,note_type,raw_text) VALUES(?,?,?,?,?)',
                            (uid(),rid,note['captured_at'],note['note_type'],note['raw_text']))
            session.update(state='completed',duration=duration);RecordingStore.write(folder/'session.json',session)
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
