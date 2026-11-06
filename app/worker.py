from .resource_policy import sync_live_demand
from .job_checkpoints import JobCheckpoints,file_digest,validate_segments,ResourceInterrupted,model_files
import fcntl, hashlib, json, math, os, subprocess, threading, time
from pathlib import Path
from .db import uid, encode
from .resources import request_for,ResourceYielded as Yielded,PRIORITIES,REASONS
from .live_results import plan_ranges, RATE
from .providers import WhisperCpp, get_asr_provider, LocalDiarization, LocalPunctuation, attach_speakers, merge_overlapping_chunks, checkpoint_identity_matches
from .analysis import LMStudioProvider,OpenAICompatibleProvider,MeetingAnalysis,chunk_segments,save_meeting_analysis,validate_citations,TEMPLATE_VERSION

class Cancelled(Exception): pass
class DispatchDeferred(Yielded):pass

class Worker:
    def __init__(self, store):
        self.store=store; self.stop=threading.Event(); self.thread=None; self.busy_jid=None; self.lock_file=None;self.asr_lock=None;self.release_model=None;self.resources=None;self.resource_claim=None;self.nonblocking_dispatch=False;self.observed_jobs=set();self.minutes_jobs=None
    def start(self):
        self.lock_file=(self.store.root/'worker.lock').open('a')
        try: fcntl.flock(self.lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close(); self.lock_file=None
            raise RuntimeError('此数据目录已有应用运行，请使用已打开的窗口')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO job_runtime_events(job_id,action,from_state) SELECT id,'recover','running' FROM jobs WHERE state='running'")
            db.execute("UPDATE jobs SET state='queued',stage='等待恢复，检查点将重新校验' WHERE state='running'")
        self.thread=threading.Thread(target=self.loop,daemon=True); self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=10)
        if self.resources:
            for jid in self.observed_jobs:self.resources.clear_observed(jid)
        if self.lock_file and (not self.thread or not self.thread.is_alive()):
            self.lock_file.close(); self.lock_file=None
    def check(self,jid):
        job=self.store.one('SELECT state FROM jobs WHERE id=?',(jid,))
        if not job or job['state']=='cancelled':raise Cancelled()
        if self.stop.is_set():raise ResourceInterrupted()
    def run(self,jid,args,log,extra_env=None,timeout=None):
        self.boundary(jid)
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
    def boundary(self,jid):
        self.check(jid)
        claim=self.resource_claim
        if self.resources and claim and self.resources.should_yield(claim.workload,claim.requirements):raise Yielded()
    def cancel_waiting(self,jid):
        if self.resources:self.resources.cancel_waiting(jid,{'transcription','analysis','minutes','note_expansion','terminology','index','qa','legacy'})
    def update(self,jid,stage,progress):
        self.check(jid)
        self.store.execute('UPDATE jobs SET stage=?,progress=? WHERE id=?',(stage,progress,jid))
    def next_job(self):
        live=bool(self.store.one("SELECT 1 FROM live_sessions WHERE status IN ('running','paused','draining') LIMIT 1"))
        if self.resources:
            sync_live_demand(self.resources,self.store)
            owners=[r['id'] for r in self.store.all("SELECT id FROM jobs WHERE state='queued' AND kind='retrieval_index' ORDER BY created,rowid LIMIT 128")] if live else []
            total=self.store.one("SELECT count(*) AS n FROM jobs WHERE state='queued' AND kind='retrieval_index'")['n'] if live else 0
            self.resources.set_index_deferred(owners,total)
        if live:
            self.store.execute("UPDATE jobs SET stage='实时识别期间暂缓索引' WHERE state='queued' AND kind='retrieval_index' AND stage!='实时识别期间暂缓索引'")
        jobs=self.store.all("SELECT * FROM jobs WHERE state='queued'"+(" AND COALESCE(kind,'transcription')!='retrieval_index'" if live else '')+" ORDER BY created,rowid")
        if not self.resources:return jobs[0] if jobs else None
        active={job['id'] for job in jobs}
        for jid in self.observed_jobs-active:self.resources.clear_observed(jid)
        self.observed_jobs=active
        # SQLite preserves same-priority order. Probes never reserve partial slots.
        jobs.sort(key=lambda job:PRIORITIES[self.resource_spec(job)[0]])
        for job in jobs:
            workload,requirements=self.dispatch_spec(job)
            reason=self.resources.admission_reason(workload,requirements)
            if not reason:
                self.resources.clear_observed(job['id']);return job
            self.resources.observe_wait(job['id'],workload,requirements)
        return None
    def dispatch_spec(self,job):
        workload,requirements=self.resource_spec(job)
        if 'cloud_llm' not in requirements:requirements={'legacy_inference':1,**requirements}
        return workload,requirements
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
                self.nonblocking_dispatch=self.resources is not None
                self.process(job)
                stage={'analysis':'分析完成','note_expansion':'简记扩写完成','retrieval_index':'检索索引建立完成','retrieval_answer':'引用问答完成','minutes':'AI 纪要草稿已保存，待人工核对','terminology':'术语候选已整理，待确认'}.get(job.get('kind'),'转写完成')
                if job.get('kind')=='analysis' and json.loads(job['config']).get('minutes_followup'):stage='分析与纪要草稿完成'
                self.store.execute("UPDATE jobs SET state='completed',stage=?,progress=1 WHERE id=? AND state='running'",(stage,job['id']))
            except ResourceInterrupted:
                self.store.execute("UPDATE jobs SET state='queued',stage='服务已停止，等待恢复' WHERE id=? AND state='running'",(job['id'],))
            except Cancelled: pass
            except DispatchDeferred as error:
                self.store.execute("UPDATE jobs SET state='queued',stage=? WHERE id=? AND state='running'",(str(error),job['id']))
            except Yielded:
                self.store.execute("UPDATE jobs SET state='queued',stage='会中识别优先，等待继续' WHERE id=? AND state='running'",(job['id'],))
            except Exception as e:
                self.store.execute("UPDATE jobs SET state='failed',error=?,stage='处理失败' WHERE id=? AND state='running'",(str(e),job['id']))
            finally:self.busy_jid=None;self.nonblocking_dispatch=False
    def resource_spec(self,job):
        kind=job.get('kind') or 'transcription'
        workload={'retrieval_index':'index','retrieval_answer':'qa'}.get(kind,kind)
        if workload not in ('transcription','analysis','minutes','note_expansion','terminology','index','qa'):workload='legacy'
        try:config=json.loads(job.get('config') or '{}')
        except (ValueError,TypeError):config={}
        if workload=='qa' and isinstance(config,dict) and config.get('qa_id'):
            row=self.store.one('SELECT config FROM qa_runs WHERE id=?',(config['qa_id'],))
            if row:
                try:config=json.loads(row['config'])
                except (ValueError,TypeError):config={}
        provider=config.get('provider',{}) if isinstance(config,dict) else {}
        llm='cloud_llm' if isinstance(provider,dict) and provider.get('provider_type')=='cloud' else 'local_llm'
        requirements={'asr':1} if workload=='transcription' else ({'embedding':1} if workload=='index' else {llm:1})
        return workload,requirements

    def process(self,job):
        current=self.store.one('SELECT state FROM jobs WHERE id=?',(job['id'],))
        if not current or current['state']=='completed':return
        self.check(job['id'])
        claim=None
        try:
            if self.asr_lock:
                waiting=False;last_wait=''
                workload,requirements=self.dispatch_spec(job)
                claim=self.resources.request(workload,requirements,job['id']) if self.resources else request_for(self.asr_lock,workload,requirements,job['id'])
                if self.nonblocking_dispatch and not claim.acquire(False):
                    self.resources.observe_wait(job['id'],workload,requirements)
                    raise DispatchDeferred(REASONS.get(claim.reason,'等待资源调度'))
                while not self.nonblocking_dispatch and not claim.acquire(timeout=.2):
                    self.check(job['id'])
                    reason=self.resources.waiting_for(job['id']) if self.resources else None
                    message=reason['message'] if reason else '等待语音识别资源'
                    if not waiting or message!=last_wait:self.update(job['id'],message,job.get('progress',0));waiting=True;last_wait=message
                self.resource_claim=claim
                self.boundary(job['id'])
                if waiting:self.update(job['id'],'准备处理',job.get('progress',0))
            from .local_embedding import local_embedding
            if 'cloud_llm' not in self.resource_spec(job)[1]:
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
                if json.loads(job['config']).get('operation'):
                    if not self.minutes_jobs:raise RuntimeError('纪要后台服务未装配')
                    return self.minutes_jobs.process(self,job)
                from .auto_minutes import process
                return process(self,job)
            return self.process_transcription(job)
        except (Cancelled,Yielded,ResourceInterrupted):raise
        except Exception:
            if job.get('kind') in (None,'transcription'):
                try:
                    from .transcription_recovery import preserve_partial
                    preserve_partial(self.store,job)
                except Exception:pass # Recovery must not mask the original inference error.
            raise
        finally:
            if claim:claim.close()
            self.resource_claim=None

    def process_note_expansion(self,job):
        from .note_expansion import generate_expansion,publish,Expansion,validate_expansion
        config=json.loads(job['config']);jid=job['id'];mid=job['meeting_id']
        profile=self.store.one('SELECT * FROM llm_configs WHERE id=?',(config.get('profile_id'),)) if config.get('profile_id') else None
        if config.get('profile_id') and not profile:raise RuntimeError('模型配置已删除')
        if profile:
            snapshot=config.get('provider',{})
            profile.update({key:snapshot[key] for key in ('provider_type','base_url') if key in snapshot})
        provider=OpenAICompatibleProvider(profile) if profile else LMStudioProvider()
        work=self.store.directory(mid)/jid;work.mkdir(exist_ok=True)
        segments=config['segments'];self.update(jid,'整理简记与原文依据',.1)
        checkpoints=JobCheckpoints(self.store,job)
        result=checkpoints.load('expansion',config,lambda value:validate_expansion(Expansion.model_validate(value),segments))
        if result is None:
            if not segments:result=Expansion(status='insufficient',text='',reason='简记附近没有可用文字稿，请完成转写或调整简记时间。',evidence=[])
            else:result=generate_expansion(provider,config['model'],{key:config[key] for key in ('raw_text','note_type','captured_at')}|{'note_scope':config.get('note_scope','personal')},segments,work/'expansion',lambda args,extra=None:self.run(jid,args,work/'worker.log',extra))
        checkpoints.save('expansion',result.model_dump(),config,lambda value:validate_expansion(Expansion.model_validate(value),segments))
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
        checkpoints=JobCheckpoints(self.store,job)
        log=work/'worker.log';run=lambda args,extra_env=None:self.run(jid,args,log,extra_env)
        chunks=chunk_segments(segments,int(config.get('max_chars',8000)))
        results=[]
        for index,chunk in enumerate(chunks):
            self.update(jid,f'分析文字稿 {index+1}/{len(chunks)}',.05+.7*index/max(1,len(chunks)))
            def validate_map(value):
                result=MeetingAnalysis.model_validate(value);validate_citations(result,{s['id'] for s in chunk});return result
            result=checkpoints.load(f'map-{index:04d}',chunk,validate_map)
            if result is None:
                self.boundary(jid)
                result=provider.analyze_chunk(model,chunk,work/f'map-{index:04d}',run)
                validate_citations(result,{s['id'] for s in chunk})
                checkpoints.save(f'map-{index:04d}',result.model_dump(),chunk,validate_map)
            results.append(result)
        round_number=0
        while len(results)>1:
            groups=[results[i:i+6] for i in range(0,len(results),6)];merged=[]
            for index,group in enumerate(groups):
                self.update(jid,f'合并分析结果 {round_number+1}.{index+1}',.76+.18*(index+1)/max(1,len(groups)))
                inputs=[item.model_dump() for item in group]
                def validate_reduce(value):
                    result=MeetingAnalysis.model_validate(value);validate_citations(result,{sid for source in group for item in source.all_items() for sid in item.source_segment_ids});return result
                result=checkpoints.load(f'reduce-{round_number:02d}-{index:04d}',inputs,validate_reduce)
                if result is None:
                    self.boundary(jid)
                    result=provider.reduce(model,group,work/f'reduce-{round_number:02d}-{index:04d}',run)
                    valid={sid for source in group for item in source.all_items() for sid in item.source_segment_ids}
                    validate_citations(result,valid)
                    checkpoints.save(f'reduce-{round_number:02d}-{index:04d}',result.model_dump(),inputs,validate_reduce)
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
        checkpoints=JobCheckpoints(self.store,job)
        source_identity={'sha256':file_digest(source)}
        self.update(jid,'准备音频',.02)
        prepared=not checkpoints.file_valid('audio',audio,source_identity)
        if prepared:
            temp=work/'audio.tmp.wav'
            run(['ffmpeg','-v','error','-y','-i',str(source),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(temp)])
            temp.replace(audio)
        import wave
        with wave.open(str(audio)) as wav:
            if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,16000):raise ValueError('准备音频格式无效，未提交检查点')
            duration=wav.getnframes()/wav.getframerate()
        if duration <= 0: raise RuntimeError('录音没有可用音频')
        if prepared:checkpoints.save_file('audio',audio,source_identity)
        self.store.execute('UPDATE meetings SET duration=? WHERE id=?',(duration,mid))
        if config.get('completion_mode')!='quick' and not provider.ready(): raise RuntimeError(f'{provider.label} 尚未就绪，请检查运行环境')
        reuse_meta={}
        if config.get('completion_mode')=='quick':
            segments,reuse_meta=self.fast_transcription(config,duration,audio,work,provider,run,jid,checkpoints,source_identity)
        else:
            # Fixed chunks with context overlap; merge using both timing and text.
            chunk_seconds=config.get('chunk_seconds',provider.chunk_seconds); count=math.ceil(duration/chunk_seconds); chunks=[]
            for i in range(count):
                self.update(jid,f'识别与对齐片段 {i+1}/{count}' if provider.engine_id=='qwen' else f'转写片段 {i+1}/{count}',.08+.78*i/count)
                start=max(0,i*chunk_seconds-2); end=min(duration,(i+1)*chunk_seconds+2)
                inputs=[source_identity,provider.identity(),start,end]
                part=checkpoints.load(f'chunk-{i}',inputs,lambda value:validate_segments(value,duration))
                if part is None:
                    chunk=work/f'chunk-{i}.wav'
                    run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(audio),'-t',str(end-start),'-c:a','pcm_s16le',str(chunk)])
                    part=provider.transcribe(chunk,work/f'chunk-{i}',run)
                    owned=[]
                    for s in part:
                        s['start']+=start; s['end']=min(duration,s['end']+start)
                        s['id']=uid();s.setdefault('speaker','未分配');owned.append(s)
                    part=owned
                    checkpoints.save(f'chunk-{i}',part,inputs,lambda value:validate_segments(value,duration))
                    chunk.unlink(missing_ok=True)
                chunks.append(part)
            segments=merge_overlapping_chunks(chunks)
            segments.sort(key=lambda s:s['start'])
        punctuation=LocalPunctuation()
        if config.get('completion_mode')!='quick' and config.get('punctuation',provider.needs_punctuation) and punctuation.ready() and segments:
            self.update(jid,'恢复中文标点',.87)
            inputs=[segments,model_files([punctuation.model,Path(__file__).resolve().parents[1]/'scripts/punctuate.py'])]
            saved=checkpoints.load('punctuated',inputs,lambda value:validate_segments(value,duration))
            if saved is None:
                segments=punctuation.process(segments,work,run)
                checkpoints.save('punctuated',segments,inputs,lambda value:validate_segments(value,duration))
            else:segments=saved
        if config.get('completion_mode')!='quick' and config.get('diarization'):
            self.update(jid,'分析发言人',.9)
            diar=work/'speakers.json'
            diarizer=LocalDiarization()
            inputs=[source_identity,config.get('num_speakers',0),model_files([diarizer.segmentation,diarizer.embedding,Path(__file__).resolve().parents[1]/'scripts/diarize.py'])]
            if not checkpoints.file_valid('speakers',diar,inputs):
                diarizer.diarize(audio,diar,run,config.get('num_speakers',0))
                checkpoints.save_file('speakers',diar,inputs)
            segments=attach_speakers(segments,json.loads(diar.read_text()))
        self.check(jid)
        if file_digest(source)!=source_identity['sha256']:raise ValueError('原音文件已变化，结果未发布；请核对音频后重试')
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

    def fast_transcription(self,config,duration,audio,work,provider,run,jid,checkpoints,source_identity):
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
            inputs=[source_identity,provider.identity(),start,end]
            part=checkpoints.load(f'fast-{index}',inputs,lambda value:validate_segments(value,duration))
            if part is None:
                chunk=work/f'fast-{index}.wav'
                run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(audio),'-af','apad','-t',str(max(.5,end-start)),'-c:a','pcm_s16le',str(chunk)])
                with wave.open(str(chunk),'rb') as wav:silent=not any(wav.readframes(wav.getnframes()))
                raw=[] if silent else getattr(provider,'transcribe_live',provider.transcribe)(chunk,work/f'fast-{index}',run)
                part=[dict(s,id=uid(),speaker='未分配',start=min(end,start+max(0,s['start'])),end=min(end,start+max(0,s['end'])))
                      for s in raw if s['text'].strip() and s['start']<end-start]
                checkpoints.save(f'fast-{index}',part,inputs,lambda value:validate_segments(value,duration))
                chunk.unlink(missing_ok=True)
            chunks.append(part)
        segments=sorted((s for chunk in chunks for s in chunk),key=lambda s:s['start'])
        return segments,{'kind':'live_reuse','completion_mode':'quick','reused_ranges':len(reused),
            'reused_seconds':sum((r['end_frame']-r['start_frame'])/RATE for r in reused),
            'patched_ranges':[{'start':a,'end':b} for a,b in missing],
            'range_ids':[r['id'] for r in reused],'timestamp_precision':'draft','diarization':False}
