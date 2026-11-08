"""Recording-bound ASR supervisor. Subscribers never own inference lifetime."""
from .languages import AUTO_LANGUAGE_ERROR, configure_asr, recording_language
from .resource_policy import sync_live_demand
from array import array
import asyncio,hashlib,io,json,math,os,shutil,time,unicodedata,wave
from contextlib import aclosing
from .db import encode
from .live_batch import whisper_events
from .live_policy import sentence_hint,update_load
from .live_stream import qwen_events,inspect_audio
from .live_results import RATE,plan_ranges
from .live_segmentation import acoustic_pause,elastic_plan,HARD_SECONDS,PAUSE_SECONDS
from .track_audio import StreamingEchoCanceller

MAX_EVENTS=192

def stable_prefix(previous,current,reserve=6):
    """Conservative display hint only: never grants durable audio coverage."""
    n=0
    for a,b in zip(previous,current):
        if a!=b:break
        n+=1
    n=min(n,max(0,len(current)-reserve))
    while n and (current[n-1]=='\u200d' or (n<len(current) and (unicodedata.combining(current[n]) or current[n]=='\u200d' or current[n]=='\ufe0f'))):n-=1
    return current[:n]

def preview_step(processing_ms):
    return max(2,min(5,math.ceil(processing_ms/1000*1.3)))

def pcm_wav(raw):
    result=io.BytesIO()
    with wave.open(result,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(RATE);wav.writeframes(raw)
    return result.getvalue()

class LiveSessions:
    def __init__(self,store,recordings,native,cache,asr_lock,resident,provider,batch_provider=None):
        self.store=store;self.recordings=recordings;self.native=native;self.cache=cache
        self.asr_lock=asr_lock;self.resident=resident;self.provider=provider
        self.batch_provider=batch_provider
        self.tasks={};self.wakes={};self.closed=False
    def row(self,rid):
        row=self.store.one('SELECT * FROM live_sessions WHERE recording_id=?',(rid,))
        if not row:raise ValueError('实时会话尚未创建')
        return row
    def emit(self,rid,kind,**data):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT seq FROM live_sessions WHERE recording_id=?',(rid,)).fetchone()
            if not row:return
            seq=row['seq']+1;config=json.loads(db.execute('SELECT config FROM live_sessions WHERE recording_id=?',(rid,)).fetchone()['config'])
            event={'type':kind,'seq':seq,'protocol_version':1,'recording_id':rid,'engine':config.get('engine','qwen'),**data}
            db.execute('UPDATE live_sessions SET seq=?,updated=CURRENT_TIMESTAMP WHERE recording_id=?',(seq,rid))
            db.execute('INSERT INTO live_session_events(recording_id,seq,payload) VALUES(?,?,?)',(rid,seq,encode(event)))
            db.execute('DELETE FROM live_session_events WHERE recording_id=? AND seq<=?',(rid,seq-MAX_EVENTS))
        if rid in self.wakes:self.wakes[rid].set()
    def sync_resources(self):
        scheduler=getattr(self.asr_lock,'scheduler',None)
        if scheduler:sync_live_demand(scheduler,self.store)
    def checkpoint(self,rid,**values):
        if values:self.store.execute('UPDATE live_sessions SET '+','.join(k+'=?' for k in values)+' WHERE recording_id=?',(*[encode(v) if isinstance(v,(dict,list)) else v for v in values.values()],rid))
        if 'status' in values:self.sync_resources()
    def snapshot(self,rid):
        row=self.row(rid);segments=[]
        session=self.recordings.read(rid)
        if session.get('source')=='macos':
            try:duration=self.native.status(rid,touch=False).get('duration',session['duration'])
            except (OSError,ValueError):duration=session['duration']
        else:duration=max(session['duration'],self.input_end(rid)/RATE)
        rows,_=plan_ranges(self.cache.snapshot(rid,json.loads(row['config']).get('engine','qwen'),json.loads(row['config']).get('source_language','zh')),duration)
        for record in rows:
            segments.extend(dict(s,live_range_id=record['id'],live_segment_index=i) for i,s in enumerate(record['segments']))
        return {'type':'snapshot','protocol_version':1,'recording_id':rid,'engine':json.loads(row['config']).get('engine','qwen'),'seq':row['seq'],'status':row['status'],'segments':segments,
                'partial':json.loads(row['partial']),'cursor':row['cursor']/RATE,'metrics':json.loads(row['metrics']),
                'source':row['source'],'config':json.loads(row['config'])}
    def recovery_status(self,rid):
        """Passive projection: no ownership renewal, inference, or input devices."""
        row=self.row(rid);session=self.recordings.read(rid);capture={}
        try:capture=json.loads((self.recordings.folder(rid)/'capture.json').read_text())
        except (OSError,ValueError):pass
        config=json.loads(row['config']);task=self.tasks.get(rid)
        active=bool(task and not task.done());duration=max(session.get('duration',0),capture.get('duration',0))
        if row['source']=='browser':duration=max(duration,self.input_end(rid)/RATE)
        coverage=self.cache.overview(rid,config.get('engine','qwen'),duration)
        running=row['source']=='native' and capture.get('state') in ('recording','paused')
        published=session['state']=='completed' or bool(self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)))
        recoverable=running and not active and row['status']!='draining' and not published and not self.closed
        return {'recording_id':rid,'seq':row['seq'],'engine':config.get('engine','qwen'),'status':row['status'],
            'capture_state':capture.get('state',session['state']),'task_active':active,'recoverable':recoverable,
            'confirmed_until':row['cursor']/RATE,'lag_seconds':max(0,duration-row['cursor']/RATE),
            'coverage':coverage,'recovery':json.loads(row['metrics']).get('recovery'),
            'state':'needs_recovery' if recoverable else 'catching_up' if active and duration-row['cursor']/RATE>3 else 'running' if active else 'ended'}

    async def recover(self,rid,expected_seq):
        # No await before ensure: the event loop serializes the CAS and task creation.
        if self.closed:raise ValueError('服务正在关闭')
        row=self.row(rid)
        if row['seq']!=expected_seq:raise ValueError('识别状态已变化，请刷新后重试')
        status=self.recovery_status(rid)
        if not status['recoverable']:raise ValueError('当前识别无需恢复或录音已结束')
        if self.native.status(rid,touch=False).get('state') not in ('recording','paused'):raise ValueError('采集未运行，已有结果保留')
        metrics=json.loads(row['metrics']);metrics['recovery']={'reason':'manual','state':'resuming','from_frame':row['cursor'],'requested_at':time.time()}
        self.checkpoint(rid,status='running',partial={},metrics=metrics)
        self.emit(rid,'status',message='从最后确认位置恢复识别；历史缺段仍保留待补')
        self.ensure(rid)
        return self.recovery_status(rid)

    async def start(self,rid,config):
        if self.closed:raise ValueError('服务正在关闭')
        config={'engine':'qwen','prefix_enabled':True,'echo_mode':'standard',**config}
        config['source_language']=recording_language(self.recordings,rid,config.get('source_language'))
        if config['engine'] not in ('qwen','whisper'):raise ValueError('不支持的实时识别引擎')
        if config['engine']=='whisper':config.update(responsive=False,prefix_enabled=False,context_enabled=False,terms='')
        session=self.recordings.read(rid)
        if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):raise ValueError('录音已保存，不能重启实时识别')
        source='native' if session.get('source')=='macos' else 'browser'
        if source!='native' or not session.get('include_microphone'):
            config['echo_mode']='standard'
        if source=='native' and self.native.status(rid,touch=False).get('state') not in ('recording','paused'):raise ValueError('原生采集未运行，已有结果仍可查看')
        row=self.store.one('SELECT * FROM live_sessions WHERE recording_id=?',(rid,))
        if row and row['status']=='stopped':raise ValueError('实时会话已结束，已有结果仍可查看')
        if row and {'engine':'qwen','prefix_enabled':True,'echo_mode':'standard','source_language':'zh',**json.loads(row['config'])}!=config:raise ValueError('正在运行的实时会话不能更换识别配置')
        if not row:
            # Adopt old completed ranges without re-recognizing them after an upgrade.
            ranges,_=plan_ranges(self.cache.snapshot(rid,config['engine'],config['source_language']),86400)
            cursor=max((r['end_frame'] for r in ranges),default=0)
            self.store.execute("INSERT INTO live_sessions(recording_id,source,config,cursor) VALUES(?,?,?,?)",(rid,source,encode(config),cursor))
        self.ensure(rid)
        return self.snapshot(rid)
    def ensure(self,rid):
        self.sync_resources()
        if rid not in self.tasks or self.tasks[rid].done():
            task=asyncio.create_task(self.run(rid));self.tasks[rid]=task
            def done(completed):
                if self.tasks.get(rid) is completed:self.tasks.pop(rid,None)
            task.add_done_callback(done)
    async def resume(self):
        for row in self.store.all("SELECT * FROM live_sessions WHERE status!='stopped'"):
            rid=row['recording_id']
            try:
                session=self.recordings.read(rid)
                if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):self.checkpoint(rid,status='stopped',partial={});continue
                metrics=json.loads(row['metrics']);metrics['recovery']={'reason':'service_restart','state':'resuming','from_frame':row['cursor'],'requested_at':time.time()}
                self.checkpoint(rid,partial={},metrics=metrics)
                self.emit(rid,'status',message='服务已恢复，从最后确认位置继续识别')
                self.ensure(rid)
            except (OSError,ValueError):self.checkpoint(rid,status='stopped',partial={})
    def input_end(self,rid):
        row=self.store.one('SELECT MAX(end_frame) AS last FROM live_pcm_inputs WHERE recording_id=?',(rid,))
        return row['last'] or 0
    def ingest(self,rid,offset,payload):
        row=self.row(rid)
        if row['source']!='browser' or row['status'] not in ('running','paused'):raise ValueError('当前会话不接受浏览器音频')
        duration,_=inspect_audio(payload);start=round(offset*RATE);end=start+round(duration*RATE)
        if end>86400*RATE:raise ValueError('录音时间超出范围')
        digest=hashlib.sha256(payload).hexdigest()
        with self.recordings.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            session=self.recordings.read(rid)
            if session['state']=='completed' or db.execute('SELECT id FROM meetings WHERE id=?',(rid,)).fetchone():raise ValueError('会议已保存，不再接收实时音频')
            rows=db.execute('SELECT * FROM live_pcm_inputs WHERE recording_id=? AND start_frame<? AND end_frame>?',(rid,end,start)).fetchall()
            if rows:
                if len(rows)==1 and rows[0]['start_frame']==start and rows[0]['end_frame']==end and rows[0]['digest']==digest:return {'ok':True,'cached':True}
                raise ValueError('实时音频范围冲突，原有音频已保留')
            folder=self.recordings.folder(rid)/'live-input';folder.mkdir(exist_ok=True)
            path=folder/f'{start}-{end}.wav';tmp=path.with_suffix('.tmp')
            with tmp.open('wb') as handle:handle.write(payload);handle.flush();os.fsync(handle.fileno())
            tmp.replace(path)
            db.execute('INSERT INTO live_pcm_inputs(recording_id,start_frame,end_frame,digest) VALUES(?,?,?,?)',(rid,start,end,digest))
        return {'ok':True,'cached':False}
    def browser_window(self,rid,cursor,target,draining=False,paused=False,responsive=True,sentence_end=None):
        rows=self.store.all('SELECT * FROM live_pcm_inputs WHERE recording_id=? AND end_frame>? ORDER BY start_frame',(rid,cursor))
        if not rows:return {'ready':False}
        # Bound live backlog; skipped intervals remain gaps in durable coverage.
        limit=HARD_SECONDS if responsive else 15
        horizon=rows[-1]['end_frame'];begin=max(cursor,horizon-limit*RATE) if horizon-cursor>2*limit*RATE else cursor
        begin=max(begin,rows[0]['start_frame']);raw=b'';position=begin
        if begin!=cursor:sentence_end=None
        for row in rows:
            if row['end_frame']<=position:continue
            if row['start_frame']>position:break
            with wave.open(str(self.recordings.folder(rid)/'live-input'/f"{row['start_frame']}-{row['end_frame']}.wav"),'rb') as wav:
                wav.setpos(position-row['start_frame']);part=wav.readframes(min(row['end_frame']-position,limit*RATE-len(raw)//2))
            raw+=part;position=begin+len(raw)//2
            if len(raw)>=2*limit*RATE:break
        if responsive:
            plan=elastic_plan(raw,target,'stop' if draining else 'pause' if paused else None,sentence_end)
            if plan is None:return {'ready':False}
            count=plan.pop('count');raw=raw[:count*2];position=begin+count
        else:
            if len(raw)<round((.5 if draining or paused else 15)*RATE)*2:return {'ready':False}
            plan={'confirmed':True,'boundary_reason':'stop' if draining else 'pause' if paused else 'standard','leading_silence_seconds':0,'extending':False}
        return {'ready':True,'offset':begin/RATE,'next_offset':position/RATE,'skipped_seconds':(begin-cursor)/RATE,
                'payload':pcm_wav(raw),**plan}
    def continuation(self,rid,window,break_at):
        start=round(window['offset']*RATE)
        previous=self.store.one("SELECT segments FROM live_asr_ranges WHERE recording_id=? AND engine='qwen' AND state='completed' AND end_frame=? ORDER BY rowid DESC LIMIT 1",(rid,start))
        segments=json.loads(previous['segments']) if previous else []
        last=segments[-1] if segments else {}
        linked=bool(last.get('continuation_id') and last.get('continues_next') and window.get('leading_silence_seconds',0)<PAUSE_SECONDS and
                    not window.get('skipped_seconds') and break_at!=start and round(last.get('end',-1)*RATE)==start)
        return {'continuation_id':last['continuation_id'] if linked else rid+':'+str(start),
                'continues_previous':linked,'continues_next':window.get('boundary_reason')=='hard_limit',
                'boundary_reason':window.get('boundary_reason','standard')}
    def link_track_windows(self,rid,window,segments,payloads,break_at,failed_tracks):
        start=round(window['offset']*RATE)
        end=round(window['next_offset']*RATE)
        previous=self.store.one("SELECT segments FROM live_asr_ranges WHERE recording_id=? AND engine='qwen' AND state='completed' AND end_frame=? ORDER BY rowid DESC LIMIT 1",(rid,start))
        prior=json.loads(previous['segments']) if previous else []
        frames=round(PAUSE_SECONDS*RATE)
        for track,payload in payloads.items():
            parts=[part for part in segments if part['source_track']==track]
            if not parts:continue
            last=next((part for part in reversed(prior) if part.get('source_track')==track),{})
            with wave.open(io.BytesIO(payload),'rb') as wav:
                head=wav.readframes(frames)
                wav.setpos(max(0,wav.getnframes()-frames))
                tail=array('h')
                tail.frombytes(wav.readframes(frames))
            tail.reverse()
            first,final=parts[0],parts[-1]
            linked=bool(last.get('continues_next') and last.get('continuation_id') and
                        not window.get('skipped_seconds') and break_at!=start and
                        round(last['end']*RATE)==round(first['start']*RATE)==start and
                        acoustic_pause(head)[1]<PAUSE_SECONDS)
            key=last['continuation_id'] if linked else f"{rid}:{start}:{track}"
            first.update(continuation_id=key,continues_previous=linked)
            final.update(continuation_id=key,boundary_reason=window['boundary_reason'],
                         continues_next=not failed_tracks and window['boundary_reason']=='hard_limit' and
                         round(final['end']*RATE)==end and acoustic_pause(tail.tobytes())[1]<PAUSE_SECONDS)

    async def recognize_tracks(self,rid,config,window,on_partial=None,previous_tracks=None,echo=None,*,break_at=-1):
        """Preview each track independently, then persist the reconciled window."""
        payloads=window.pop('track_payloads')
        engine=config['engine']
        factory=self.batch_provider if engine=='whisper' else self.provider
        if not factory:raise ValueError('实时识别引擎尚未配置')
        provider=configure_asr(factory(),config['source_language'])
        from .track_audio import ECHO_VERSION, reduce_echo_wav, remove_echo_duplicates, wav_echo_evidence, limit_asr_repetition
        identity={'model':provider.identity(),'track_mode':'dual-v1','segmentation_version':2,'echo_version':ECHO_VERSION}
        duration=window['next_offset']-window['offset']
        confirmed=window.get('confirmed',True)
        aid=cached=None
        started=time.monotonic()
        failed_tracks=[]
        first_text_ms=None
        def publish_partial(part):
            nonlocal first_text_ms
            if first_text_ms is None:first_text_ms=round((time.monotonic()-started)*1000)
            if on_partial:on_partial(part)
        try:
            original_mic=payloads['microphone']
            raw_tracks=b''.join(payloads.values()) if confirmed else None
            if echo is not None:
                payloads['microphone']=await asyncio.to_thread(
                    echo.process,payloads['system'],original_mic,round(window['offset']*RATE))
                if not echo.failed:
                    identity['echo_mode']='aec3'
                    if confirmed:identity['cleaned_sha256']=hashlib.sha256(payloads['microphone']).hexdigest()
            if confirmed:
                aid,cached=self.cache.begin(rid,'native',engine,window['offset'],duration,raw_tracks,identity)
            if cached is None:
                segments=[]
                if echo is None:
                    payloads['microphone']=await asyncio.to_thread(reduce_echo_wav,payloads['system'],original_mic)
                for track,payload in payloads.items():
                    previous_track=(previous_tracks or {}).get(track,{})
                    continuation={
                        'key':f"{rid}:{round(window['offset']*RATE)}:{track}",
                        'enabled':config.get('prefix_enabled',True) and config.get('responsive',False),
                        'confirmed':confirmed,
                        'previous_text':previous_track.get('text',''),
                        'stable_text':previous_track.get('stable_text',''),
                    }
                    common=(provider,self.cache,self.asr_lock,payload,window['offset'],rid,'native',self.store.root,window)
                    iterator=(whisper_events(*common,persist=False) if engine=='whisper'
                              else qwen_events(*common,resident=self.resident,confirmed=confirmed,context_enabled=False,
                                               continuation=continuation,persist=False))
                    last_sent=0
                    async with aclosing(iterator):
                        async for line in iterator:
                            event=json.loads(line)
                            if event['type']=='error':
                                if AUTO_LANGUAGE_ERROR in event['message']:
                                    failed_tracks.append(track)
                                    break
                                raise RuntimeError(event['message'])
                            if event['type']=='partial' and event['text'] and time.monotonic()-last_sent>=.15:
                                publish_partial({'source_track':track,'start':event['start'],'end':event['end'],'text':limit_asr_repetition(event['text'])})
                                last_sent=time.monotonic()
                            if event['type']=='final':
                                parts=[dict(part,text=limit_asr_repetition(part['text'])) for part in event['result']['segments']]
                                segments.extend(dict(part,source_track=track) for part in parts)
                                if parts:
                                    text=' '.join(part['text'] for part in parts)
                                    preview={'source_track':track,'start':parts[0]['start'],
                                             'end':parts[-1]['end'],'text':text}
                                    if engine=='qwen' and config.get('responsive'):
                                        preview['stable_text']=stable_prefix(previous_track.get('text',''),text)
                                    publish_partial(preview)
                                break
                        else:raise RuntimeError('独立音轨未返回识别结果')
                if confirmed:
                    offset=window['offset']
                    segments=await asyncio.to_thread(remove_echo_duplicates,segments,lambda start,end:
                        wav_echo_evidence(io.BytesIO(payloads['system']),io.BytesIO(original_mic),start-offset,end-offset),mark_mixed=True)
                    segments.sort(key=lambda item:item['start'])
                    if engine=='qwen' and 'boundary_reason' in window:
                        self.link_track_windows(rid,window,segments,payloads,break_at,failed_tracks)
                    if failed_tracks:self.cache.partial(aid,segments,failed_tracks)
                    else:self.cache.complete(aid,segments)
                    segments=[dict(part,live_range_id=aid,live_segment_index=i) for i,part in enumerate(segments)]
                cached=segments
            return {**window,'engine':engine,'segments':cached,'duration':duration,'confirmed':confirmed,
                    'processing_ms':round((time.monotonic()-started)*1000),'first_text_ms':first_text_ms,
                    'persisted':confirmed,'decoder':{},'failed_tracks':failed_tracks,
                    'echo_mode':identity.get('echo_mode','standard')},None
        except asyncio.CancelledError:
            if aid:self.cache.fail(aid,'双轨识别中断，原音已保留')
            raise
        except (RuntimeError,OSError,ValueError) as error:
            if aid:self.cache.fail(aid,str(error))
            return None,str(error)

    @staticmethod
    def track_preview(window,tracks):
        return {'start':window['offset'],'end':window['next_offset'],
                'text':'\n'.join(part['text'] for part in tracks.values()),
                'tracks':list(tracks.values()),'boundary_reason':window['boundary_reason']}

    async def run(self,rid):
        previous='';stable='';preview_end=0;preview_start=None;next_target=3;errors=0
        dual_preview={}
        previous_tracks={}
        echo=StreamingEchoCanceller() if json.loads(self.row(rid)['config']).get('echo_mode')=='aec3' else None
        saved_metrics=json.loads(self.row(rid)['metrics']);totals=dict(saved_metrics.get('decoder_totals',{}))
        break_at=saved_metrics.get('continuation_break_at',-1)
        load=saved_metrics.get('load',{});resource_notice=0
        next_target=max(3,load.get('preview_step_seconds',3))
        try:
            while not self.closed:
                row=self.row(rid)
                if row['status']=='stopped':return
                session=self.recordings.read(rid)
                if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):break
                break_at=max(break_at,json.loads(row['metrics']).get('continuation_break_at',-1))
                draining=row['status']=='draining';config=json.loads(row['config']);cursor=row['cursor']
                self.sync_resources()
                if self.asr_lock.locked():
                    if hasattr(self.asr_lock,'observe_wait'):self.asr_lock.observe_wait(rid)
                    if time.monotonic()-resource_notice>5:
                        self.emit(rid,'status',message='等待识别资源；原音持续保存，已确认文字保留')
                        resource_notice=time.monotonic()
                    await asyncio.sleep(.3);continue
                if hasattr(self.asr_lock,'clear_wait'):self.asr_lock.clear_wait(rid)
                hint=sentence_hint(previous,(preview_end-cursor)/RATE) if previous and preview_start==cursor else None
                if row['source']=='native':
                    capture=await asyncio.to_thread(self.native.status,rid,touch=False)
                    if capture.get('state') not in ('recording','paused','stopped','interrupted','failed'):
                        self.emit(rid,'status',message='采集已中断；已确认文字保留，剩余音频可在快速完成时补识别');break
                    if capture.get('state') in ('stopped','interrupted','failed'):draining=True
                    available=max(0,capture.get('duration',0)-cursor/RATE)
                    target=min(HARD_SECONDS,max(next_target,math.floor(available))) if config['responsive'] else 15
                    dual=self.recordings.read(rid).get('include_microphone',False)
                    if dual:
                        # A short acoustic pause is a boundary only when each decoded track ends a sentence.
                        hints=[sentence_hint(part['text'],(preview_end-cursor)/RATE) for part in previous_tracks.values()]
                        hint=hints[0] if preview_start==cursor and hints and all(h is not None for h in hints) else None
                    window=await asyncio.to_thread(self.native.draft_window,rid,cursor/RATE,
                        next_target if dual and config['responsive'] else target,
                        config['responsive'],True,sentence_end=hint,split_tracks=dual)
                    paused=capture.get('state')=='paused'
                else:
                    if not draining and row['status']=='running' and self.input_end(rid)<=preview_end:
                        await asyncio.sleep(.3);continue
                    window=await asyncio.to_thread(self.browser_window,rid,cursor, next_target if config['responsive'] else 15,draining,row['status']=='paused',config['responsive'],hint)
                    paused=row['status']=='paused'
                if not window['ready']:
                    if paused and break_at!=cursor:
                        break_at=cursor
                        state_metrics=json.loads(self.row(rid)['metrics']);state_metrics['continuation_break_at']=break_at
                        self.checkpoint(rid,metrics=state_metrics)
                    if draining:break
                    await asyncio.sleep(.3);continue
                start=round(window['offset']*RATE)
                if preview_start!=start:
                    previous='';stable='';preview_end=0;preview_start=start
                    dual_preview.clear()
                    previous_tracks.clear()
                end=round(window['next_offset']*RATE)
                if end<=preview_end and not window['confirmed']:
                    await asyncio.sleep(.3);continue
                window.setdefault('boundary_reason','stop' if draining else 'pause' if paused else 'standard')
                if draining:
                    window['confirmed']=True
                    if window.get('boundary_reason')=='preview':window.update(boundary_reason='stop',extending=False)
                dual_window='track_payloads' in window
                if dual_window:
                    def publish_track(part):
                        track=part['source_track']
                        old=dual_preview.get(track,{})
                        # A completed decode can legitimately revise the preview to shorter text.
                        if 'stable_text' not in part and len(part['text'])<len(old.get('text','')) and part['end']>=old.get('end',0):
                            return
                        if 'stable_text' not in part and config['engine']=='qwen' and config['responsive']:
                            prefix=previous_tracks.get(track,{}).get('stable_text','')
                            part=dict(part,stable_text=prefix if part['text'].startswith(prefix) else '')
                        dual_preview[track]=part
                        partial=self.track_preview(window,dual_preview)
                        self.checkpoint(rid,partial=partial)
                        self.emit(rid,'partial',partial=partial)
                    was_failed=echo.failed if echo is not None else False
                    result,failure=await self.recognize_tracks(rid,config,window,publish_track,previous_tracks,echo,break_at=break_at)
                    if echo is not None and echo.failed and not was_failed:
                        self.emit(rid,'status',message='AEC3 不可用，已切回标准回声处理')
                else:
                    if config.get('engine','qwen')=='qwen':window.update(self.continuation(rid,window,break_at))
                    payload=window.pop('payload');window.pop('silent',None);result=None;failure=None;last_sent=0
                    iterator=None if config.get('engine')=='whisper' else qwen_events(configure_asr(self.provider(),config.get('source_language')),self.cache,self.asr_lock,payload,window['offset'],rid,row['source'],self.store.root,window,
                        resident=self.resident,confirmed=window['confirmed'],context_enabled=config['context_enabled'],terms=config['terms'],continuation={'key':rid+':'+str(round(window['offset']*RATE)),'enabled':config.get('prefix_enabled',True) and config['responsive'],
                        'confirmed':window['confirmed'],'previous_text':previous,'stable_text':stable})
                    if config.get('engine')=='whisper':
                        if not self.batch_provider:raise ValueError('Whisper 托管识别尚未配置')
                        iterator=whisper_events(configure_asr(self.batch_provider(),config.get('source_language')),self.cache,self.asr_lock,payload,window['offset'],rid,row['source'],self.store.root,window)
                    async with aclosing(iterator):
                        async for line in iterator:
                            event=json.loads(line)
                            if event['type']=='partial' and time.monotonic()-last_sent>=.15:
                                text=event['text'];prefix=stable if text.startswith(stable) else ''
                                self.emit(rid,'partial',partial={'start':event['start'],'end':event['end'],'text':text,'stable_text':prefix,**{k:window[k] for k in ('continuation_id','continues_previous','continues_next','boundary_reason')}})
                                last_sent=time.monotonic()
                            elif event['type']=='final':result=event['result']
                            elif event['type']=='error':failure=event['message']
                if result is None:
                    errors+=1;self.emit(rid,'status',message=failure or '实时识别未完成，原音已保留')
                    if draining:break
                    await asyncio.sleep(min(8,2**min(errors,3)));continue
                errors=0;text=''.join(s['text'] for s in result['segments'])
                stable=stable_prefix(previous,text);previous=text;preview_end=end
                metrics={k:result.get(k) for k in ('processing_ms','first_text_ms','duration','confirmed','skipped_seconds','decoder','boundary_reason','extending')}
                if dual_window:metrics['echo_mode']=result['echo_mode']
                recovery=json.loads(self.row(rid)['metrics']).get('recovery')
                if recovery:
                    metrics['recovery']=dict(recovery,state=('catching_up' if result['confirmed'] else 'resuming'))
                decoder=result.get('decoder',{})
                if decoder:
                    for key,hit in [('prefix_rounds',decoder.get('mode')=='prefix'),('feature_hits',decoder.get('feature_cache_hit',False)),('fallbacks',decoder.get('fallback',False))]:
                        totals[key]=totals.get(key,0)+int(bool(hit))
                metrics['continuation_break_at']=break_at
                metrics['decoder_totals']=dict(totals);result['decoder_totals']=dict(totals)
                if row['source']=='native':
                    try:capture=await asyncio.to_thread(self.native.status,rid,touch=False)
                    except (OSError,ValueError):pass
                horizon=capture.get('duration',0) if row['source']=='native' else self.input_end(rid)/RATE
                backlog=max(0,horizon-window['next_offset'])
                load=update_load(load,result['processing_ms'],result['duration'],backlog)
                metrics['policy_version']=2;metrics['load']=load;metrics['preview_step_seconds']=load['preview_step_seconds']
                result['load']=load
                if result['confirmed']:
                    if recovery:metrics['recovery']['state']='resumed' if backlog<=3 else 'catching_up'
                    self.checkpoint(rid,cursor=end,partial={},metrics=metrics)
                    self.emit(rid,'final',result=result)
                    if result.get('failed_tracks'):
                        labels='、'.join('电脑声音' if track=='system' else '麦克风' for track in result['failed_tracks'])
                        self.emit(rid,'status',message=f'{labels}有一段语言未识别，原音已保留。'+AUTO_LANGUAGE_ERROR)
                    previous='';stable='';preview_end=0;preview_start=None;next_target=max(3,load['preview_step_seconds'])
                else:
                    if dual_window:
                        dual_preview.clear()
                        for part in result['segments']:
                            track=part['source_track']
                            if track not in dual_preview:dual_preview[track]=dict(part)
                            else:
                                dual_preview[track]['text']+=' '+part['text']
                                dual_preview[track]['end']=part['end']
                        if config['engine']=='qwen' and config['responsive']:
                            for track,part in dual_preview.items():
                                part['stable_text']=stable_prefix(previous_tracks.get(track,{}).get('text',''),part['text'])
                            previous_tracks=dict(dual_preview)
                        partial=self.track_preview(window,dual_preview)
                    else:
                        partial={'start':window['offset'],'end':window['next_offset'],'text':text,'stable_text':stable,**{k:window[k] for k in ('continuation_id','continues_previous','continues_next','boundary_reason')}}
                    self.checkpoint(rid,partial=partial,metrics=metrics)
                    self.emit(rid,'partial',partial=partial,metrics=metrics)
                    next_target=min(HARD_SECONDS,math.floor(window['next_offset']-window['offset'])+load['preview_step_seconds'])
        except asyncio.CancelledError:raise
        except (OSError,ValueError) as error:
            self.emit(rid,'status',message='实时会话暂时中断，原音与已确认文字保留：'+str(error)[:200])
        except Exception:
            self.emit(rid,'status',message='实时会话暂时中断，原音与已确认文字保留，可重新连接')
        finally:
            if hasattr(self.asr_lock,'clear_wait'):self.asr_lock.clear_wait(rid)
            if not self.closed:
                self.checkpoint(rid,status='stopped',partial={})
                self.emit(rid,'status',message='实时会话已结束，未覆盖音频可在快速完成时补识别')
    def pause_boundary(self,rid,frame):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT metrics FROM live_sessions WHERE recording_id=?',(rid,)).fetchone()
            if row:
                metrics=json.loads(row['metrics']);metrics['continuation_break_at']=frame
                db.execute('UPDATE live_sessions SET metrics=? WHERE recording_id=?',(encode(metrics),rid))
    async def finish(self,rid,timeout=4):
        row=self.store.one('SELECT * FROM live_sessions WHERE recording_id=?',(rid,))
        if not row or row['status']=='stopped':return
        self.checkpoint(rid,status='draining')
        task=self.tasks.get(rid)
        if not task or task.done():self.ensure(rid);task=self.tasks[rid]
        try:await asyncio.wait_for(asyncio.shield(task),timeout)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():raise
        except asyncio.TimeoutError:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    def published(self,rid):
        if not self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):return
        folder=self.recordings.folder(rid)/'live-input'
        if folder.exists():shutil.rmtree(folder)
        self.store.execute('DELETE FROM live_pcm_inputs WHERE recording_id=?',(rid,))
    async def close(self):
        self.closed=True
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    async def events(self,rid,after):
        self.row(rid);wake=self.wakes.setdefault(rid,asyncio.Event())
        rows=self.store.all('SELECT seq,payload FROM live_session_events WHERE recording_id=? AND seq>? ORDER BY seq',(rid,after))
        latest=self.row(rid)['seq']
        if after==0 or after>latest or not rows or rows[0]['seq']!=after+1:
            snapshot=self.snapshot(rid);after=snapshot['seq'];yield encode(snapshot)+'\n'
        while not self.closed:
            wake.clear()
            rows=self.store.all('SELECT seq,payload FROM live_session_events WHERE recording_id=? AND seq>? ORDER BY seq',(rid,after))
            if rows and rows[0]['seq']!=after+1:
                snapshot=self.snapshot(rid);after=snapshot['seq'];yield encode(snapshot)+'\n';continue
            for row in rows:after=row['seq'];yield row['payload']+'\n'
            if self.row(rid)['status']=='stopped':yield encode({'type':'ended','seq':after,'protocol_version':1,'recording_id':rid,'engine':json.loads(self.row(rid)['config']).get('engine','qwen')})+'\n';return
            try:await asyncio.wait_for(wake.wait(),10)
            except asyncio.TimeoutError:yield encode({'type':'heartbeat','seq':after,'protocol_version':1,'recording_id':rid,'engine':json.loads(self.row(rid)['config']).get('engine','qwen')})+'\n'
