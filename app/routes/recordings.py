"""Recordings HTTP routes; shared services are supplied by the composition root."""
from ..resources import ResourceBusy
from ..chinese_record import TranslationInput, live_source, translate_segment
from ..languages import configure_asr, recording_language, SourceLanguage
import sqlite3, io, json, subprocess, tempfile, time, wave
from pathlib import Path
from typing import Literal
from fastapi import File, Form, UploadFile, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse, Response
from starlette.concurrency import run_in_threadpool
from ..db import uid, encode
from ..live_stream import inspect_audio, qwen_events
from ..recording_completion import completion_report, recording_quality
from ..http_schemas import RecoverLiveSession, RecoverCaptureControl, CaptureStateInput, KeepAwakeInput, LiveCorrectionDecision, LiveCorrectionInput, LiveSessionInput, LiveSessionState, NativeCommandInput, NativeDraftInput, NativeRecordingInput, RecordingFinish, RecordingInput, RecordingMetadata, RecordingQualityReviewInput
from ..application_services import ApplicationServices

def register_routes(app,services:ApplicationServices):
    capture_probes=services.capture_probes
    decorated=services.meetings.decorated
    keep_awake=services.keep_awake
    lifecycle=services.lifecycle
    live_asr_lock=services.live_asr_lock
    live_corrections=services.live_corrections
    live_results=services.live_results
    live_sessions=services.live_sessions
    llm_config=services.configurations.get
    meeting=services.meetings.meeting
    native_capture=services.native_capture
    recording_commands=services.recording_commands
    recording_health=services.recording_health
    recording_open=services.meetings.recording_open
    recording_runtime=services.recording_runtime
    recordings=services.recordings
    resident=services.resident
    store=services.store
    worker=services.worker
    get_asr_provider=services.providers.asr

    @app.post('/api/live-transcribe')
    async def live_transcribe(file:UploadFile=File(...),offset:float=Form(default=0,ge=0,le=86400,allow_inf_nan=False),
                              engine:Literal['whisper','qwen']=Form(default='whisper'),
                              recording_id:str|None=Form(default=None),source:Literal['browser','native']=Form(default='browser'),
                              source_language:SourceLanguage|None=Form(default=None)):
        started=time.monotonic();aid=None;locked=False
        try:
            provider=configure_asr(get_asr_provider(engine),recording_language(recordings,recording_id,source_language))
            payload=await file.read(4*1024*1024+1)
            if len(payload)>4*1024*1024:raise HTTPException(413,'实时音频片段最大支持 4MB')
            if not payload:raise HTTPException(400,'实时音频片段为空')
            try:
                with wave.open(io.BytesIO(payload),'rb') as wav:
                    duration=wav.getnframes()/wav.getframerate()
                    valid=wav.getnchannels()==1 and wav.getsampwidth()==2 and wav.getframerate()==16000
                    samples=wav.readframes(wav.getnframes())
                    if len(samples)!=wav.getnframes()*2:valid=False
            except (wave.Error,EOFError):raise HTTPException(400,'实时音频必须是有效的 WAV 文件')
            if not valid:raise HTTPException(400,'实时音频必须是 16 kHz 单声道 PCM')
            if duration<.5 or duration>45:raise HTTPException(400,'实时音频片段时长应在 0.5–45 秒之间')
            def response(segments,cached=False):return {'segments':segments,'duration':duration,'engine':engine,
                'processing_ms':round((time.monotonic()-started)*1000),'cached':cached,'persisted':bool(recording_id)}
            if recording_id:
                try:aid,cached=live_results.begin(recording_id,source,engine,offset,duration,payload,provider.identity())
                except FileNotFoundError as error:raise HTTPException(404,str(error))
                except ValueError as error:raise HTTPException(409,str(error))
                if cached is not None:return response(cached,True)
            if not any(samples):segments=[]
            else:
                if not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'上一段实时草稿仍在识别')
                locked=True
                if not provider.ready():raise HTTPException(409,f'{provider.label} 实时草稿模型尚未就绪')
                with tempfile.TemporaryDirectory(prefix='meeting-live-',dir=store.root) as folder_name:
                    folder=Path(folder_name);audio=folder/'chunk.wav';audio.write_bytes(payload);log=folder/(engine+'.log')
                    def run(args):
                        with log.open('ab') as handle:
                            try:result=subprocess.run(args,stdout=handle,stderr=handle,timeout=90,check=False)
                            except subprocess.TimeoutExpired:raise RuntimeError('实时草稿识别超时')
                        if result.returncode:raise RuntimeError('实时草稿识别失败：'+log.read_text(errors='replace')[-800:])
                    try:part=await run_in_threadpool(getattr(provider,'transcribe_live',provider.transcribe),audio,folder/'result',run)
                    except RuntimeError as error:raise HTTPException(503,str(error))
                    segments=[{'start':round(offset+max(0,min(duration,s['start'])),3),
                        'end':round(offset+max(0,min(duration,s['end'])),3),'text':s['text']} for s in part if s['text'].strip()]
            if aid:
                live_results.complete(aid,segments)
                segments=[dict(s,live_range_id=aid,live_segment_index=i) for i,s in enumerate(segments)]
            return response(segments)
        except BaseException as error:
            if aid:live_results.fail(aid,getattr(error,'detail',str(error)) or '识别请求中断')
            raise
        finally:
            await file.close()
            if locked:live_asr_lock.release()


    def stream_response(events):
        return StreamingResponse(events,media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


    @app.post('/api/live-transcribe/stream')
    async def live_transcribe_stream(file:UploadFile=File(...),offset:float=Form(default=0,ge=0,le=86400,allow_inf_nan=False),
                                    recording_id:str|None=Form(default=None),confirmed:bool=Form(default=True),
                                    context_enabled:bool=Form(default=True),terms:str=Form(default="",max_length=1000),
                                    source_language:SourceLanguage|None=Form(default=None)):
        try:
            payload=await file.read(4*1024*1024+1)
            inspect_audio(payload)
            if recording_id:live_results.check_session(recording_id,'browser')
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(400,str(error))
        finally:await file.close()
        return stream_response(qwen_events(configure_asr(get_asr_provider('qwen'),recording_language(recordings,recording_id,source_language)),live_results,live_asr_lock,payload,offset,recording_id,'browser',store.root,resident=resident,confirmed=confirmed,context_enabled=context_enabled,terms=terms))


    @app.get('/api/recordings')
    def list_recordings():return [row|{'health':recording_runtime.inspect(row['id'])} for row in recordings.list()]


    @app.post('/api/recordings/{rid}/recovery-archive')
    def archive_empty_recording(rid:str,archived:bool=True):
        try:
            with native_capture.lock,recordings.lock:
                report=recording_health.snapshot(rid)
                if not report['empty_session']:raise HTTPException(409,'仅可收起已停止且没有音频、简记、准备内容或识别结果的空会话')
                value=recordings.read(rid);value['recovery_archived']=archived
                recordings.write(recordings.folder(rid)/'session.json',value)
            return {'ok':True}
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')


    @app.get('/api/live-model')
    def live_model_status():return resident.status()|{'formal_job_running':bool(worker.busy_jid)}


    @app.post('/api/live-model/{action}')
    async def live_model_control(action:Literal['warm','release']):
        if worker.busy_jid or not live_asr_lock.acquire(blocking=False):raise HTTPException(409,'正式处理或实时识别正在使用资源，请稍后重试')
        try:
            if action=='warm':await resident.warm(get_asr_provider('qwen'))
            else:await resident.close()
            return resident.status()
        except (RuntimeError,TimeoutError) as error:raise HTTPException(409,str(error) or '模型加载超时')
        finally:live_asr_lock.release()


    @app.get('/api/native-capture')
    def native_capability():return native_capture.capability()


    @app.post('/api/native-capture',status_code=201)
    def native_start(body:NativeRecordingInput):
        try:
            with capture_probes.lock:
                if capture_probes.active():raise ValueError('正在试录，请先完成或取消测试')
                session=native_capture.start(body.microphone)
            native_capture.set_keep_awake(session['id'],body.keep_awake)
            if body.keep_awake:keep_awake.acquire(session['id'])
            return session
        except (ValueError,OSError) as error:raise HTTPException(409,str(error))


    @app.get('/api/native-capture/{rid}')
    def native_status(rid:str):
        try:return native_capture.status(rid)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))


    @app.post('/api/native-capture/{rid}/draft')
    async def native_draft(rid:str,body:NativeDraftInput):
        try:window=await run_in_threadpool(native_capture.draft_window,rid,body.offset)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        if not window['ready']:
            return window|{'segments':[],'duration':0,'processing_ms':0}
        payload=window.pop('payload')
        window.pop('silent')
        result=await live_transcribe(UploadFile(file=io.BytesIO(payload),filename='native-draft.wav'),window['offset'],body.engine,rid,'native',body.source_language)
        return window|result


    @app.post('/api/native-capture/{rid}/draft/stream')
    async def native_draft_stream(rid:str,body:NativeDraftInput):
        if body.engine!='qwen':raise HTTPException(400,'逐字推送仅支持 Qwen，请使用 Whisper 整批接口')
        try:window=await run_in_threadpool(native_capture.draft_window,rid,body.offset,body.window_seconds,body.responsive)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        if not window['ready']:
            async def waiting():
                yield encode({'type':'final','seq':0,'request_id':uid(),'recording_id':rid,'engine':'qwen',
                    'result':window|{'segments':[],'duration':0,'processing_ms':0}})+'\n'
            return stream_response(waiting())
        payload=window.pop('payload');window.pop('silent')
        return stream_response(qwen_events(configure_asr(get_asr_provider('qwen'),recording_language(recordings,rid,body.source_language)),live_results,live_asr_lock,payload,window['offset'],rid,'native',store.root,window,resident=resident,confirmed=window.get('confirmed',True),context_enabled=body.context_enabled,terms=body.terms))


    @app.post('/api/native-capture/{rid}/recover-control')
    def recover_capture_control(rid:str,body:RecoverCaptureControl):
        try:return native_capture.recover_control(rid,body.expected_generation)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except (OSError,ValueError) as error:raise HTTPException(409,str(error))

    @app.post('/api/native-capture/{rid}/command')
    def native_command(rid:str,body:NativeCommandInput):
        try:
            result=recording_commands.execute(rid,body.command,source='native')
            return result['native']
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except (ValueError,RuntimeError) as error:raise HTTPException(409,str(error))


    @app.post('/api/native-capture/{rid}/finish')
    async def native_finish(rid:str):
        try:
            await run_in_threadpool(recording_commands.execute,rid,'stop',source='native')
            await live_sessions.finish(rid)
            mid=await run_in_threadpool(native_capture.finish,rid);keep_awake.release(rid);return decorated(meeting(mid))
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except OSError:
            lifecycle.record('storage.failed',rid)
            raise HTTPException(503,'会议文件写盘未完成，原始音轨保留，可重试保存')
        except (ValueError,RuntimeError,subprocess.SubprocessError) as error:raise HTTPException(409,str(error))


    @app.get('/api/native-recordings')
    def active_native_recordings():
        result=[]
        for rid in list(native_capture.processes):
            try:
                if not native_capture.is_running(rid):continue
                session=recordings.read(rid)
                session['duration']=native_capture.status(rid,touch=False).get('duration',session['duration'])
                row=store.one("SELECT config FROM live_sessions WHERE recording_id=? AND status!='stopped'",(rid,))
                result.append({'recording':session,'config':json.loads(row['config']) if row else None})
            except (OSError,ValueError):continue
        return result


    @app.get('/api/live-sessions')
    def active_live_sessions():
        result=[]
        for row in store.all("SELECT * FROM live_sessions WHERE status!='stopped' AND source='native'"):
            try:
                if native_capture.is_running(row['recording_id']):result.append({'recording':recordings.read(row['recording_id']),'config':json.loads(row['config'])})
            except (OSError,ValueError):pass
        return result


    @app.put('/api/recordings/{rid}/live-session')
    async def start_live_session(rid:str,body:LiveSessionInput):
        try:return await live_sessions.start(rid,body.model_dump())
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))


    @app.get('/api/recordings/{rid}/live-session')
    def live_session_snapshot(rid:str):
        try:return live_sessions.snapshot(rid)
        except (FileNotFoundError,ValueError) as error:raise HTTPException(404,str(error))


    @app.post('/api/recordings/{rid}/live-session/recover')
    async def recover_live_session(rid:str,body:RecoverLiveSession):
        try:return await live_sessions.recover(rid,body.expected_seq)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))


    @app.get('/api/recordings/{rid}/live-audio/{aid}/{index}')
    def live_track_audio(rid:str,aid:str,index:int):
        row=store.one("SELECT segments,start_frame,end_frame FROM live_asr_ranges WHERE id=? AND recording_id=? AND state='completed'",(aid,rid))
        parts=json.loads(row['segments']) if row else []
        if not 0<=index<len(parts):raise HTTPException(404,'找不到这句原音')
        part=parts[index];track=part.get('source_track')
        if track not in ('system','microphone'):raise HTTPException(404,'这句没有独立音轨')
        start=max(row['start_frame'],round(part['start']*16000))
        count=min(row['end_frame'],round(part['end']*16000),start+45*16000)-start
        if count<=0:raise HTTPException(404,'找不到这句原音')
        try:
            try:
                with (recordings.folder(rid)/(track+'.pcm')).open('rb') as pcm:
                    pcm.seek(start*2);frames=pcm.read(count*2)
            except FileNotFoundError:
                with wave.open(str(store.directory(rid)/(track+'.wav')),'rb') as wav:
                    wav.setpos(start);frames=wav.readframes(count)
        except (FileNotFoundError,ValueError,EOFError,wave.Error):
            raise HTTPException(404,'这段音轨暂不可回听')
        frames=frames[:len(frames)//2*2]
        if not frames:raise HTTPException(404,'这段音轨暂不可回听')
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(frames)
        return Response(output.getvalue(),media_type='audio/wav',headers={'Cache-Control':'no-store'})

    @app.get('/api/recordings/{rid}/live-session/events')
    async def live_session_events(rid:str,after:int=0):
        if after<0:raise HTTPException(400,'事件序号无效')
        try:live_sessions.row(rid)
        except ValueError as error:raise HTTPException(404,str(error))
        return stream_response(live_sessions.events(rid,after))


    @app.post('/api/recordings/{rid}/live-session/audio')
    async def live_session_audio(rid:str,file:UploadFile=File(...),offset:float=Form(ge=0,le=86400,allow_inf_nan=False)):
        try:
            payload=await file.read(4*1024*1024+1)
            return await run_in_threadpool(live_sessions.ingest,rid,offset,payload)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        finally:await file.close()


    @app.post('/api/recordings/{rid}/live-session/state')
    def live_session_state(rid:str,body:LiveSessionState):
        try:
            row=live_sessions.row(rid)
            if row['source']!='browser' or row['status'] not in ('running','paused'):raise ValueError('当前会话不能切换状态')
            live_sessions.checkpoint(rid,status='paused' if body.paused else 'running')
            if body.paused:live_sessions.pause_boundary(rid,live_sessions.input_end(rid))
            return {'ok':True}
        except ValueError as error:raise HTTPException(409,str(error))


    @app.post('/api/recordings/{rid}/live-session/finish')
    async def finish_live_session(rid:str):
        await live_sessions.finish(rid)
        try:return live_sessions.snapshot(rid)
        except (FileNotFoundError,ValueError):return {'status':'stopped'}


    @app.post('/api/recordings/{rid}/chinese-record/{aid}/{index}')
    def translate_live_segment(rid:str,aid:str,index:int,body:TranslationInput):
        try:
            return translate_segment(services,rid,lambda:live_source(services,rid,aid,index),body,'live_translation')
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        except HTTPException:raise
        except ResourceBusy as error:
            raise HTTPException(409,{'code':'translation_busy','message':'本地模型忙碌，将自动继续'}) from error
        except (ValueError,RuntimeError) as error:raise HTTPException(409,str(error))
        except (OSError,TypeError):raise HTTPException(503,'本地翻译暂不可用，原文已保留')


    @app.get('/api/recordings/{rid}/corrections')
    def correction_history(rid:str):
        try:recordings.read(rid)
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        return live_corrections.list(rid)


    @app.post('/api/recordings/{rid}/corrections')
    def propose_live_correction(rid:str,body:LiveCorrectionInput):
        try:return live_corrections.propose(rid,body.range_id,body.segment_index,llm_config(body.profile_id))
        except HTTPException:raise
        except (ValueError,RuntimeError,TimeoutError) as error:raise HTTPException(409,str(error))
        except Exception:raise HTTPException(503,'本地纠错服务暂时不可用，原始识别结果已保留')


    @app.put('/api/recordings/{rid}/corrections/{cid}')
    def decide_live_correction(rid:str,cid:str,body:LiveCorrectionDecision):
        try:return live_corrections.decide(rid,cid,body.state)
        except ValueError as error:raise HTTPException(409,str(error))


    @app.get('/api/recordings/{rid}/live-results')
    def recording_live_results(rid:str,engine:Literal['whisper','qwen']='whisper'):
        try:return live_results.overview(rid,engine)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))


    @app.post('/api/recordings',status_code=201)
    def create_recording(body:RecordingInput):return recordings.create(body.mime_type)


    @app.get('/api/recordings/{rid}/health')
    def recording_inventory(rid:str):
        try:
            value=recording_runtime.inspect(rid)
            return value|{'lifecycle':{'available':lifecycle.available,'events':lifecycle.events(rid,20),'checkpoints':lifecycle.checkpoints(rid),'anomalies':lifecycle.anomalies(rid)}}
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        except ValueError:raise HTTPException(409,'录音元数据无法读取，已有文件保留')


    @app.get('/api/meetings/{mid}/recording-quality')
    def meeting_recording_quality(mid:str,view:Literal['all','pending','gaps']='all',offset:int=Query(default=0,ge=0,le=10000)):
        row=meeting(mid)
        return recording_quality(recordings,mid,float(row['duration']),pending_only=view=='pending',offset=offset,gaps_only=view=='gaps')


    @app.post('/api/meetings/{mid}/recording-quality/{aid}/review')
    def review_recording_quality(mid:str,aid:int,body:RecordingQualityReviewInput):
        meeting(mid)
        if native_capture.is_running(mid):raise HTTPException(409,'录音尚在进行，请结束后核对')
        try:lifecycle.review_anomaly(mid,aid,body.status,body.expected_version)
        except LookupError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        except RuntimeError as error:raise HTTPException(503,str(error))
        return {'ok':True}


    @app.get('/api/recordings/{rid}/completion')
    def recording_completion(rid:str,engine:Literal['whisper','qwen']='qwen'):
        try:return completion_report(store,recordings,live_results,rid,engine)
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')


    @app.post('/api/recordings/{rid}/capture-state')
    def browser_capture_state(rid:str,body:CaptureStateInput):
        try:
            command={'recording':'resume','paused':'pause','stopped':'stop','interrupted':'report_interrupted'}[body.state]
            result=recording_commands.execute(rid,command,observed_state=body.state,source='browser')
            return {'ok':True,'control':result['control']}
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        except ValueError as error:raise HTTPException(409,str(error))


    @app.get('/api/recordings/{rid}/diagnostics')
    def recording_diagnostics(rid:str):
        report=recording_inventory(rid)
        return JSONResponse(report,headers={'Content-Disposition':'attachment; filename="recording-diagnostics.json"','Cache-Control':'no-store'})


    @app.post('/api/recordings/{rid}/keep-awake')
    def recording_keep_awake(rid:str,body:KeepAwakeInput):
        try:
            recordings.folder(rid)
            if not body.enabled:
                native_capture.set_keep_awake(rid,False)
                return keep_awake.release(rid)
            if not recording_open(rid):raise HTTPException(409,'录音已完成或不存在，不能保持亮屏')
            native_capture.set_keep_awake(rid,True)
            return keep_awake.acquire(rid)
        except (FileNotFoundError,ValueError) as error:raise HTTPException(409,str(error))


    @app.put('/api/recordings/{rid}/metadata')
    def save_recording_metadata(rid:str,body:RecordingMetadata):
        try:return recordings.metadata(rid,[note.model_dump() for note in body.notes],body.duration,body.chunk_count,body.brief.model_dump() if body.brief else None)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))


    @app.put('/api/recordings/{rid}/chunks/{index}')
    def save_recording_chunk(rid:str,index:int,file:UploadFile=File(...)):
        try:
            if not 0<=index<100000:raise HTTPException(422,'分段编号无效')
            payload=file.file.read(64*1024*1024+1)
            if not payload:raise HTTPException(400,'录音分段为空')
            if len(payload)>64*1024*1024:raise HTTPException(413,'单个录音分段最大支持 64MB')
            return recordings.chunk(rid,index,payload)
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        except OSError:
            lifecycle.record('storage.failed',rid)
            raise HTTPException(503,'音频分段写盘失败，已有分段保留，请重试同步')
        finally:file.file.close()


    @app.post('/api/recordings/{rid}/finish')
    async def finish_recording(rid:str,body:RecordingFinish):
        try:
            await run_in_threadpool(recording_commands.execute,rid,'stop',observed_state='stopped',source='browser')
            await live_sessions.finish(rid)
            mid=await run_in_threadpool(recordings.finish,rid,body.chunk_count);keep_awake.release(rid)
            try:await run_in_threadpool(live_sessions.published,rid)
            except (OSError,sqlite3.Error):pass
            return decorated(meeting(mid))
        except FileNotFoundError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        except OSError:
            lifecycle.record('storage.failed',rid)
            raise HTTPException(503,'会议文件写盘未完成，已有音频保留，可重试保存')
