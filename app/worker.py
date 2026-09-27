import fcntl, hashlib, json, math, os, subprocess, threading, time
from pathlib import Path
from .db import uid, encode
from .providers import WhisperCpp, get_asr_provider, LocalDiarization, LocalPunctuation, attach_speakers, merge_overlapping_chunks
from .analysis import LMStudioProvider,OpenAICompatibleProvider,MeetingAnalysis,chunk_segments,save_meeting_analysis,validate_citations,TEMPLATE_VERSION

class Cancelled(Exception): pass

class Worker:
    def __init__(self, store):
        self.store=store; self.stop=threading.Event(); self.thread=None; self.busy_jid=None; self.lock_file=None
    def start(self):
        self.lock_file=(self.store.root/'worker.lock').open('a')
        try: fcntl.flock(self.lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close(); self.lock_file=None
            raise RuntimeError('此数据目录已有应用运行，请使用已打开的窗口')
        self.store.execute("UPDATE jobs SET state='queued',stage='等待恢复' WHERE state='running'")
        self.thread=threading.Thread(target=self.loop,daemon=True); self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=10)
        if self.lock_file and (not self.thread or not self.thread.is_alive()):
            self.lock_file.close(); self.lock_file=None
    def check(self,jid):
        job=self.store.one('SELECT state FROM jobs WHERE id=?',(jid,))
        if self.stop.is_set() or not job or job['state']=='cancelled': raise Cancelled()
    def run(self,jid,args,log,extra_env=None):
        self.check(jid)
        env=os.environ.copy(); env.update(HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYANNOTE_METRICS_ENABLED='0')
        if extra_env:env.update(extra_env)
        with open(log,'ab') as handle:
            process=subprocess.Popen(args,stdout=handle,stderr=handle,env=env,pass_fds=(self.lock_file.fileno(),) if self.lock_file else ())
            try:
                while process.poll() is None:
                    self.check(jid); time.sleep(.2)
                if process.returncode:
                    tail=Path(log).read_text(errors='replace')[-1800:]
                    raise RuntimeError(f'处理进程失败（{process.returncode}）：{tail}')
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                raise
    def update(self,jid,stage,progress):
        self.check(jid)
        self.store.execute('UPDATE jobs SET stage=?,progress=? WHERE id=?',(stage,progress,jid))
    def loop(self):
        while not self.stop.is_set():
            job=self.store.one("SELECT * FROM jobs WHERE state='queued' ORDER BY created,rowid LIMIT 1")
            if not job: self.stop.wait(.5); continue
            with self.store.connect() as db:
                changed=db.execute("UPDATE jobs SET state='running' WHERE id=? AND state='queued'",(job['id'],)).rowcount
                if changed: self.busy_jid=job['id']
            if not changed: continue
            self.busy_jid=job['id']
            try:
                self.process(job)
                stage='分析完成' if job.get('kind')=='analysis' else '转写完成'
                self.store.execute("UPDATE jobs SET state='completed',stage=?,progress=1 WHERE id=? AND state='running'",(stage,job['id']))
            except Cancelled: pass
            except Exception as e:
                self.store.execute("UPDATE jobs SET state='failed',error=?,stage='处理失败' WHERE id=? AND state='running'",(str(e),job['id']))
            finally: self.busy_jid=None
    def process(self,job):
        if job.get('kind')=='analysis':return self.process_analysis(job)
        return self.process_transcription(job)

    def process_analysis(self,job):
        mid=job['meeting_id'];jid=job['id'];config=json.loads(job['config'])
        revision=int(config['revision']);model=config['model']
        row=self.store.one('SELECT segments FROM revisions WHERE meeting_id=? AND revision=?',(mid,revision))
        if not row:raise RuntimeError('分析所绑定的文稿版本不存在')
        segments=json.loads(row['segments'])
        if not segments:raise RuntimeError('文字稿为空，无法生成会议分析')
        profile_id=config.get('profile_id')
        if profile_id:
            profile=self.store.one('SELECT * FROM llm_configs WHERE id=?',(profile_id,))
            if not profile:raise RuntimeError('模型配置已删除，无法继续任务')
            snapshot=config.get('provider') or {}
            profile.update({key:snapshot[key] for key in ('provider_type','base_url') if key in snapshot})
            provider=OpenAICompatibleProvider(profile)
        else:
            provider=LMStudioProvider()
            if model not in provider.models():raise RuntimeError('所选 LM Studio 模型当前不可用')
        work=self.store.directory(mid)/jid;work.mkdir(exist_ok=True)
        log=work/'worker.log';run=lambda args,extra_env=None:self.run(jid,args,log,extra_env)
        chunks=chunk_segments(segments,int(config.get('max_chars',8000)))
        results=[]
        for index,chunk in enumerate(chunks):
            self.update(jid,f'分析文字稿 {index+1}/{len(chunks)}',.05+.7*index/max(1,len(chunks)))
            checkpoint=work/f'map-{index:04d}.json'
            if checkpoint.exists():result=MeetingAnalysis.model_validate(json.loads(checkpoint.read_text()))
            else:
                result=provider.analyze_chunk(model,chunk,work/f'map-{index:04d}',run)
                validate_citations(result,{s['id'] for s in chunk})
                temporary=checkpoint.with_suffix('.tmp');temporary.write_text(encode(result.model_dump()));temporary.replace(checkpoint)
            results.append(result)
        round_number=0
        while len(results)>1:
            groups=[results[i:i+6] for i in range(0,len(results),6)];merged=[]
            for index,group in enumerate(groups):
                self.update(jid,f'合并分析结果 {round_number+1}.{index+1}',.76+.18*(index+1)/max(1,len(groups)))
                checkpoint=work/f'reduce-{round_number:02d}-{index:04d}.json'
                if checkpoint.exists():result=MeetingAnalysis.model_validate(json.loads(checkpoint.read_text()))
                else:
                    result=provider.reduce(model,group,work/f'reduce-{round_number:02d}-{index:04d}',run)
                    valid={sid for source in group for item in source.all_items() for sid in item.source_segment_ids}
                    validate_citations(result,valid)
                    temporary=checkpoint.with_suffix('.tmp');temporary.write_text(encode(result.model_dump()));temporary.replace(checkpoint)
                merged.append(result)
            results=merged;round_number+=1
        self.check(jid);self.update(jid,'校验原文引用',.96)
        result=results[0];valid_ids={s['id'] for s in segments};validate_citations(result,valid_ids)
        save_meeting_analysis(self.store,mid,revision,result,{'provider':config.get('provider',{}).get('name','LM Studio'),'provider_type':config.get('provider',{}).get('provider_type','local'),'model':model,
            'template_version':config.get('template_version',TEMPLATE_VERSION),'job':jid,'chunks':len(chunks),
            'context_window':config.get('context_window'),'max_chars':config.get('max_chars',8000)},job_id=jid)

    def process_transcription(self,job):
        mid=job['meeting_id']; jid=job['id']; config=json.loads(job['config'])
        if config.get('pipeline_version',1)!=2: raise RuntimeError('处理流程已升级，请新建转写任务')
        folder=self.store.directory(mid); work=folder/jid; work.mkdir(exist_ok=True)
        log=work/'worker.log'; run=lambda args:self.run(jid,args,log)
        provider=get_asr_provider(config.get('engine','whisper'))
        if config.get('engine_identity') and config['engine_identity']!=provider.identity():
            raise RuntimeError('识别模型或引擎已变更，请新建转写任务，不能复用旧检查点')
        meeting=self.store.one('SELECT * FROM meetings WHERE id=?',(mid,))
        source=folder/'original'; audio=work/'audio.wav'
        self.update(jid,'准备音频',.02)
        if not audio.exists():
            temp=work/'audio.tmp.wav'
            run(['ffmpeg','-v','error','-y','-i',str(source),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(temp)])
            temp.replace(audio)
        import wave
        with wave.open(str(audio)) as wav: duration=wav.getnframes()/wav.getframerate()
        if duration <= 0: raise RuntimeError('录音没有可用音频')
        self.store.execute('UPDATE meetings SET duration=? WHERE id=?',(duration,mid))
        if not provider.ready(): raise RuntimeError(f'{provider.label} 尚未就绪，请检查运行环境')
        # Fixed chunks with context overlap; merge using both timing and text.
        chunk_seconds=config.get('chunk_seconds',provider.chunk_seconds); count=math.ceil(duration/chunk_seconds); chunks=[]
        for i in range(count):
            self.update(jid,f'识别与对齐片段 {i+1}/{count}' if provider.engine_id=='qwen' else f'转写片段 {i+1}/{count}',.08+.78*i/count)
            checkpoint=work/f'chunk-{i}.checkpoint.json'
            if checkpoint.exists(): part=json.loads(checkpoint.read_text())
            else:
                start=max(0,i*chunk_seconds-2); end=min(duration,(i+1)*chunk_seconds+2)
                chunk=work/f'chunk-{i}.wav'
                run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(audio),'-t',str(end-start),'-c:a','pcm_s16le',str(chunk)])
                part=provider.transcribe(chunk,work/f'chunk-{i}',run)
                owned=[]
                for s in part:
                    s['start']+=start; s['end']=min(duration,s['end']+start)
                    s['id']=uid(); owned.append(s)
                part=owned
                temp=checkpoint.with_suffix('.tmp'); temp.write_text(encode(part)); temp.replace(checkpoint)
                chunk.unlink(missing_ok=True)
            chunks.append(part)
        segments=merge_overlapping_chunks(chunks)
        segments.sort(key=lambda s:s['start'])
        punctuation=LocalPunctuation()
        if config.get('punctuation',provider.needs_punctuation) and punctuation.ready() and segments:
            self.update(jid,'恢复中文标点',.87)
            cached=work/'punctuated.json'
            segments=json.loads(cached.read_text()) if cached.exists() else punctuation.process(segments,work,run)
        if config.get('diarization'):
            self.update(jid,'分析发言人',.9)
            diar=work/'speakers.json'
            if not diar.exists(): LocalDiarization().diarize(audio,diar,run,config.get('num_speakers',0))
            segments=attach_speakers(segments,json.loads(diar.read_text()))
        self.check(jid)
        # Save a candidate artifact if the user changed the transcript during inference.
        source_meta=encode({'engine':provider.engine_id,'engine_label':provider.label,'model':Path(provider.model).name,
                            'aligner':Path(provider.aligner).name if hasattr(provider,'aligner') else None,
                            'language':'zh','job':jid})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
            if state['state']!='running': raise Cancelled()
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()['revision']
            if current != config['base_revision']:
                db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(uid(),mid,current,'transcript_candidate',encode(segments),source_meta))
                db.execute("UPDATE jobs SET stage='已生成候选稿，校对内容已保留',state='completed',progress=1 WHERE id=?",(jid,))
            else:
                rev=current+1; payload=encode(segments)
                db.execute('INSERT INTO revisions(meeting_id,revision,segments,source) VALUES(?,?,?,?)',(mid,rev,payload,source_meta))
                db.execute('UPDATE meetings SET revision=?,segments=? WHERE id=?',(rev,payload,mid))
                db.execute("UPDATE jobs SET state='completed',stage='转写完成',progress=1 WHERE id=?",(jid,))
