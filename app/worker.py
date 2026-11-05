import fcntl, hashlib, json, math, os, subprocess, threading, time
from pathlib import Path
from .db import uid, encode
from .live_results import plan_ranges, RATE
from .providers import WhisperCpp, get_asr_provider, LocalDiarization, LocalPunctuation, attach_speakers, merge_overlapping_chunks, checkpoint_identity_matches
from .analysis import LMStudioProvider,OpenAICompatibleProvider,MeetingAnalysis,chunk_segments,save_meeting_analysis,validate_citations,TEMPLATE_VERSION

class Cancelled(Exception): pass

class Worker:
    def __init__(self, store):
        self.store=store; self.stop=threading.Event(); self.thread=None; self.busy_jid=None; self.lock_file=None;self.asr_lock=None;self.release_model=None
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
    def run(self,jid,args,log,extra_env=None,timeout=None):
        self.check(jid)
        env=os.environ.copy(); env.update(HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYANNOTE_METRICS_ENABLED='0')
        if extra_env:env.update(extra_env)
        with open(log,'ab') as handle:
            process=subprocess.Popen(args,stdout=handle,stderr=handle,env=env,pass_fds=(self.lock_file.fileno(),) if self.lock_file else ())
            started=time.monotonic()
            try:
                while process.poll() is None:
                    if timeout is not None and time.monotonic()-started>=timeout:raise RuntimeError('模型请求超过处理时限，请缩小依据范围后重试')
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
    def next_job(self):
        live=bool(self.store.one("SELECT 1 FROM live_sessions WHERE status IN ('running','paused','draining') LIMIT 1"))
        if live:
            self.store.execute("UPDATE jobs SET stage='实时识别期间暂缓索引' WHERE state='queued' AND kind='retrieval_index' AND stage!='实时识别期间暂缓索引'")
        return self.store.one("SELECT * FROM jobs WHERE state='queued'"+(" AND COALESCE(kind,'transcription')!='retrieval_index'" if live else '')+" ORDER BY created,rowid LIMIT 1")
    def loop(self):
        while not self.stop.is_set():
            job=self.next_job()
            if not job: self.stop.wait(.5); continue
            with self.store.connect() as db:
                changed=db.execute("UPDATE jobs SET state='running' WHERE id=? AND state='queued'",(job['id'],)).rowcount
                if changed: self.busy_jid=job['id']
            if not changed: continue
            self.busy_jid=job['id']
            try:
                self.process(job)
                stage={'analysis':'分析完成','note_expansion':'简记扩写完成','retrieval_index':'检索索引建立完成','retrieval_answer':'引用问答完成','minutes':'AI 纪要草稿已保存，待人工核对','terminology':'术语候选已整理，待确认'}.get(job.get('kind'),'转写完成')
                if job.get('kind')=='analysis' and json.loads(job['config']).get('minutes_followup'):stage='分析与纪要草稿完成'
                self.store.execute("UPDATE jobs SET state='completed',stage=?,progress=1 WHERE id=? AND state='running'",(stage,job['id']))
            except Cancelled: pass
            except Exception as e:
                self.store.execute("UPDATE jobs SET state='failed',error=?,stage='处理失败' WHERE id=? AND state='running'",(str(e),job['id']))
            finally: self.busy_jid=None
    def process(self,job):
        acquired=False
        try:
            if self.asr_lock:
                waiting=False
                while not self.asr_lock.acquire(timeout=.2):
                    self.check(job['id'])
                    if not waiting:self.update(job['id'],'等待语音识别资源',job.get('progress',0));waiting=True
                acquired=True;self.check(job['id'])
                if waiting:self.update(job['id'],'准备处理',job.get('progress',0))
            from .local_embedding import local_embedding
            local_embedding().release()
            if self.release_model:self.release_model()
            if job.get('kind')=='retrieval_answer':
                from .qa import QA
                return QA(self.store).process(self,job)
            if job.get('kind')=='retrieval_index':
                from .retrieval import Retrieval
                return Retrieval(self.store).build(self,job)
            if job.get('kind')=='terminology':
                from .terminology import process_candidates
                return process_candidates(self,job)
            if job.get('kind')=='note_expansion':return self.process_note_expansion(job)
            if job.get('kind')=='analysis':return self.process_analysis(job)
            if job.get('kind')=='minutes':
                from .auto_minutes import process
                return process(self,job)
            return self.process_transcription(job)
        except Cancelled:raise
        except Exception:
            if job.get('kind') in (None,'transcription'):
                try:
                    from .transcription_recovery import preserve_partial
                    preserve_partial(self.store,job)
                except Exception:pass # Recovery must not mask the original inference error.
            raise
        finally:
            if acquired:self.asr_lock.release()

    def process_note_expansion(self,job):
        from .note_expansion import generate_expansion,publish,Expansion
        config=json.loads(job['config']);jid=job['id'];mid=job['meeting_id']
        profile=self.store.one('SELECT * FROM llm_configs WHERE id=?',(config.get('profile_id'),)) if config.get('profile_id') else None
        if config.get('profile_id') and not profile:raise RuntimeError('模型配置已删除')
        if profile:
            snapshot=config.get('provider',{})
            profile.update({key:snapshot[key] for key in ('provider_type','base_url') if key in snapshot})
        provider=OpenAICompatibleProvider(profile) if profile else LMStudioProvider()
        work=self.store.directory(mid)/jid;work.mkdir(exist_ok=True)
        segments=config['segments'];self.update(jid,'整理简记与原文依据',.1)
        if not segments:result=Expansion(status='insufficient',text='',reason='简记附近没有可用文字稿，请完成转写或调整简记时间。',evidence=[])
        else:result=generate_expansion(provider,config['model'],{key:config[key] for key in ('raw_text','note_type','captured_at')}|{'note_scope':config.get('note_scope','personal')},segments,work/'expansion',lambda args,extra=None:self.run(jid,args,work/'worker.log',extra))
        self.check(jid);self.update(jid,'核验引用',.95);publish(self.store,job,result,segments)

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
        analysis_id=save_meeting_analysis(self.store,mid,revision,result,{'provider':config.get('provider',{}).get('name','LM Studio'),'provider_type':config.get('provider',{}).get('provider_type','local'),'model':model,
            'template_version':config.get('template_version',TEMPLATE_VERSION),'job':jid,'chunks':len(chunks),
            'context_window':config.get('context_window'),'max_chars':config.get('max_chars',8000)},job_id=jid,finalize_job=not bool(config.get('minutes_followup')))
        if config.get('minutes_followup'):
            from .minutes_composition import publish_analysis,CompositionSettings
            from .minutes_templates import MinutesTemplate
            followup=config['minutes_followup']
            self.check(jid)
            publish_analysis(self.store,mid,revision,result,analysis_id,CompositionSettings.model_validate(followup['settings']),followup.get('parent_minutes_id'),template=MinutesTemplate.model_validate(followup['template']) if followup.get('template') else None,job_id=jid)


    def process_transcription(self,job):
        mid=job['meeting_id']; jid=job['id']; config=json.loads(job['config'])
        if config.get('pipeline_version',1)!=2: raise RuntimeError('处理流程已升级，请新建转写任务')
        folder=self.store.directory(mid); work=folder/jid; work.mkdir(exist_ok=True)
        log=work/'worker.log'; run=lambda args:self.run(jid,args,log)
        provider=get_asr_provider(config.get('engine','whisper'))
        if config.get('engine_identity') and not checkpoint_identity_matches(provider.engine_id,config['engine_identity'],provider.identity()):
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
        if config.get('completion_mode')!='quick' and not provider.ready(): raise RuntimeError(f'{provider.label} 尚未就绪，请检查运行环境')
        reuse_meta={}
        if config.get('completion_mode')=='quick':
            segments,reuse_meta=self.fast_transcription(config,duration,audio,work,provider,run,jid)
        else:
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
                    part=None
                    if provider.engine_id=='qwen':
                        from .transcription_recovery import recover_raw_alignment
                        part=recover_raw_alignment(work/f'chunk-{i}.qwen.raw.json',provider.model,end-start)
                    if part is None:part=provider.transcribe(chunk,work/f'chunk-{i}',run)
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
        if config.get('completion_mode')!='quick' and config.get('punctuation',provider.needs_punctuation) and punctuation.ready() and segments:
            self.update(jid,'恢复中文标点',.87)
            cached=work/'punctuated.json'
            segments=json.loads(cached.read_text()) if cached.exists() else punctuation.process(segments,work,run)
        if config.get('completion_mode')!='quick' and config.get('diarization'):
            self.update(jid,'分析发言人',.9)
            diar=work/'speakers.json'
            if not diar.exists(): LocalDiarization().diarize(audio,diar,run,config.get('num_speakers',0))
            segments=attach_speakers(segments,json.loads(diar.read_text()))
        self.check(jid)
        # Save a candidate artifact if the user changed the transcript during inference.
        source_meta=encode({'engine':provider.engine_id,'engine_label':provider.label,'model':Path(provider.model).name,
                            'aligner':Path(provider.aligner).name if hasattr(provider,'aligner') else None,
                            'language':'zh','job':jid,**reuse_meta})
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
            if state['state']!='running': raise Cancelled()
            current=db.execute('SELECT revision FROM meetings WHERE id=?',(mid,)).fetchone()['revision']
            if current != config['base_revision']:
                db.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',(uid(),mid,current,'transcript_candidate',encode(segments),source_meta))
                db.execute("UPDATE jobs SET stage=?,state='completed',progress=1 WHERE id=?",('已生成候选稿，校对内容已保留'+('；自动纪要未启动' if config.get('minutes_followup') else ''),jid))
            else:
                rev=current+1; payload=encode(segments)
                db.execute('INSERT INTO revisions(meeting_id,revision,segments,source) VALUES(?,?,?,?)',(mid,rev,payload,source_meta))
                db.execute('UPDATE meetings SET revision=?,segments=? WHERE id=?',(rev,payload,mid))
                warning_count=sum(bool(s.get('alignment_warning')) for s in segments)
                stage='快速文字稿完成，可再精细处理' if config.get('completion_mode')=='quick' else ('转写完成，部分时间待校准' if warning_count else '转写完成')
                db.execute("UPDATE jobs SET state='completed',stage=?,progress=1 WHERE id=?",(stage,jid))
                if config.get('minutes_followup'):
                    from .auto_minutes import enqueue_after_transcription
                    enqueue_after_transcription(db,mid,jid,rev,config['minutes_followup'])
            db.execute("DELETE FROM artifacts WHERE id=? AND meeting_id=? AND kind='transcript_candidate'",(hashlib.sha256(('transcription-progress:'+jid).encode()).hexdigest()[:32],mid))

    def fast_transcription(self,config,duration,audio,work,provider,run,jid):
        import wave
        reused,gaps=plan_ranges(config.get('live_snapshot',[]),duration)
        chunks=[]
        for row in reused:
            start=row['start_frame']/RATE;end=row['end_frame']/RATE
            chunks.append([dict(s,id=uid(),speaker='未分配',start=max(start,s['start']),end=min(end,s['end']))
                           for s in row['segments'] if s['start']<end and s['end']>start])
        missing=[]
        for start,end in gaps:
            while start<end-1/RATE/2:
                stop=min(end,start+15);missing.append((start,stop));start=stop
        if missing and not provider.ready():raise RuntimeError(f'需要补识别缺段/尾段，但 {provider.label} 尚未就绪；请恢复模型后重试')
        for index,(start,end) in enumerate(missing):
            self.update(jid,f'复用 {len(reused)} 段草稿 · 补识别缺段/尾段 {index+1}/{len(missing)}',.08+.78*index/max(1,len(missing)))
            checkpoint=work/f'fast-{index}.checkpoint.json'
            if checkpoint.exists():part=json.loads(checkpoint.read_text())
            else:
                chunk=work/f'fast-{index}.wav'
                run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(audio),'-af','apad','-t',str(max(.5,end-start)),'-c:a','pcm_s16le',str(chunk)])
                with wave.open(str(chunk),'rb') as wav:silent=not any(wav.readframes(wav.getnframes()))
                raw=[] if silent else getattr(provider,'transcribe_live',provider.transcribe)(chunk,work/f'fast-{index}',run)
                part=[dict(s,id=uid(),speaker='未分配',start=min(end,start+max(0,s['start'])),end=min(end,start+max(0,s['end'])))
                      for s in raw if s['text'].strip() and s['start']<end-start]
                temporary=checkpoint.with_suffix('.tmp');temporary.write_text(encode(part));temporary.replace(checkpoint)
                chunk.unlink(missing_ok=True)
            chunks.append(part)
        segments=sorted((s for chunk in chunks for s in chunk),key=lambda s:s['start'])
        return segments,{'kind':'live_reuse','completion_mode':'quick','reused_ranges':len(reused),
            'reused_seconds':sum((r['end_frame']-r['start_frame'])/RATE for r in reused),
            'patched_ranges':[{'start':a,'end':b} for a,b in missing],
            'range_ids':[r['id'] for r in reused],'timestamp_precision':'draft','diarization':False}
