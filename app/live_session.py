"""Recording-bound ASR supervisor. Subscribers never own inference lifetime."""
import asyncio,hashlib,io,json,math,os,shutil,time,unicodedata,wave
from contextlib import aclosing
from .db import encode
from .live_stream import qwen_events,inspect_audio
from .live_results import RATE,plan_ranges
from .live_segmentation import elastic_plan,HARD_SECONDS,PAUSE_SECONDS

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
    def __init__(self,store,recordings,native,cache,asr_lock,resident,provider):
        self.store=store;self.recordings=recordings;self.native=native;self.cache=cache
        self.asr_lock=asr_lock;self.resident=resident;self.provider=provider
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
            seq=row['seq']+1;event={'type':kind,'seq':seq,**data}
            db.execute('UPDATE live_sessions SET seq=?,updated=CURRENT_TIMESTAMP WHERE recording_id=?',(seq,rid))
            db.execute('INSERT INTO live_session_events(recording_id,seq,payload) VALUES(?,?,?)',(rid,seq,encode(event)))
            db.execute('DELETE FROM live_session_events WHERE recording_id=? AND seq<=?',(rid,seq-MAX_EVENTS))
        if rid in self.wakes:self.wakes[rid].set()
    def checkpoint(self,rid,**values):
        if values:self.store.execute('UPDATE live_sessions SET '+','.join(k+'=?' for k in values)+' WHERE recording_id=?',(*[encode(v) if isinstance(v,(dict,list)) else v for v in values.values()],rid))
    def snapshot(self,rid):
        row=self.row(rid);segments=[]
        session=self.recordings.read(rid)
        if session.get('source')=='macos':
            try:duration=self.native.status(rid,touch=False).get('duration',session['duration'])
            except (OSError,ValueError):duration=session['duration']
        else:duration=max(session['duration'],self.input_end(rid)/RATE)
        rows,_=plan_ranges(self.cache.snapshot(rid,'qwen'),duration)
        for record in rows:
            segments.extend(dict(s,live_range_id=record['id'],live_segment_index=i) for i,s in enumerate(record['segments']))
        return {'type':'snapshot','seq':row['seq'],'status':row['status'],'segments':segments,
                'partial':json.loads(row['partial']),'cursor':row['cursor']/RATE,'metrics':json.loads(row['metrics']),
                'source':row['source'],'config':json.loads(row['config'])}
    async def start(self,rid,config):
        if self.closed:raise ValueError('服务正在关闭')
        config={'prefix_enabled':True,**config}
        session=self.recordings.read(rid)
        if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):raise ValueError('录音已保存，不能重启实时识别')
        source='native' if session.get('source')=='macos' else 'browser'
        if source=='native' and self.native.status(rid,touch=False).get('state') not in ('recording','paused'):raise ValueError('原生采集未运行，已有结果仍可查看')
        row=self.store.one('SELECT * FROM live_sessions WHERE recording_id=?',(rid,))
        if row and row['status']=='stopped':raise ValueError('实时会话已结束，已有结果仍可查看')
        if row and {'prefix_enabled':True,**json.loads(row['config'])}!=config:raise ValueError('正在运行的实时会话不能更换识别配置')
        if not row:
            # Adopt old completed ranges without re-recognizing them after an upgrade.
            ranges,_=plan_ranges(self.cache.snapshot(rid,'qwen'),86400)
            cursor=max((r['end_frame'] for r in ranges),default=0)
            self.store.execute("INSERT INTO live_sessions(recording_id,source,config,cursor) VALUES(?,?,?,?)",(rid,source,encode(config),cursor))
        self.ensure(rid)
        return self.snapshot(rid)
    def ensure(self,rid):
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
                self.checkpoint(rid,partial={})
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
    def browser_window(self,rid,cursor,target,draining=False,paused=False,responsive=True):
        rows=self.store.all('SELECT * FROM live_pcm_inputs WHERE recording_id=? AND end_frame>? ORDER BY start_frame',(rid,cursor))
        if not rows:return {'ready':False}
        # Bound live backlog; skipped intervals remain gaps in durable coverage.
        limit=HARD_SECONDS if responsive else 15
        horizon=rows[-1]['end_frame'];begin=max(cursor,horizon-limit*RATE) if horizon-cursor>2*limit*RATE else cursor
        begin=max(begin,rows[0]['start_frame']);raw=b'';position=begin
        for row in rows:
            if row['end_frame']<=position:continue
            if row['start_frame']>position:break
            with wave.open(str(self.recordings.folder(rid)/'live-input'/f"{row['start_frame']}-{row['end_frame']}.wav"),'rb') as wav:
                wav.setpos(position-row['start_frame']);part=wav.readframes(min(row['end_frame']-position,limit*RATE-len(raw)//2))
            raw+=part;position=begin+len(raw)//2
            if len(raw)>=2*limit*RATE:break
        if responsive:
            plan=elastic_plan(raw,target,'stop' if draining else 'pause' if paused else None)
            if plan is None:return {'ready':False}
            count=plan.pop('count');raw=raw[:count*2];position=begin+count
        else:
            if len(raw)<round((.5 if draining or paused else 15)*RATE)*2:return {'ready':False}
            plan={'confirmed':True,'boundary_reason':'standard','leading_silence_seconds':0,'extending':False}
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
    async def run(self,rid):
        previous='';stable='';preview_end=0;preview_start=None;next_target=3;errors=0
        saved_metrics=json.loads(self.row(rid)['metrics']);totals=dict(saved_metrics.get('decoder_totals',{}))
        break_at=saved_metrics.get('continuation_break_at',-1)
        try:
            while not self.closed:
                row=self.row(rid)
                if row['status']=='stopped':return
                session=self.recordings.read(rid)
                if session['state']=='completed' or self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):break
                break_at=max(break_at,json.loads(row['metrics']).get('continuation_break_at',-1))
                draining=row['status']=='draining';config=json.loads(row['config']);cursor=row['cursor']
                if row['source']=='native':
                    capture=await asyncio.to_thread(self.native.status,rid,touch=False)
                    if capture.get('state') not in ('recording','paused','stopped','interrupted','failed'):
                        self.emit(rid,'status',message='采集已中断；已确认文字保留，剩余音频可在快速完成时补识别');break
                    if capture.get('state') in ('stopped','interrupted','failed'):draining=True
                    available=max(0,capture.get('duration',0)-cursor/RATE)
                    target=min(HARD_SECONDS,max(next_target,math.floor(available))) if config['responsive'] else 15
                    window=await asyncio.to_thread(self.native.draft_window,rid,cursor/RATE,target,config['responsive'],True)
                    paused=capture.get('state')=='paused'
                else:
                    if not draining and row['status']=='running' and self.input_end(rid)<=preview_end:
                        await asyncio.sleep(.3);continue
                    window=await asyncio.to_thread(self.browser_window,rid,cursor, next_target if config['responsive'] else 15,draining,row['status']=='paused',config['responsive'])
                    paused=row['status']=='paused'
                if not window['ready']:
                    if paused and break_at!=cursor:
                        break_at=cursor
                        state_metrics=json.loads(self.row(rid)['metrics']);state_metrics['continuation_break_at']=break_at
                        self.checkpoint(rid,metrics=state_metrics)
                    if draining:break
                    await asyncio.sleep(.3);continue
                start=round(window['offset']*RATE)
                if preview_start!=start:previous='';stable='';preview_end=0;preview_start=start
                end=round(window['next_offset']*RATE)
                if end<=preview_end and not window['confirmed']:
                    await asyncio.sleep(.3);continue
                if draining:
                    window['confirmed']=True
                    if window.get('boundary_reason')=='preview':window.update(boundary_reason='stop',extending=False)
                window.update(self.continuation(rid,window,break_at))
                payload=window.pop('payload');window.pop('silent',None);result=None;failure=None;last_sent=0
                iterator=qwen_events(self.provider(),self.cache,self.asr_lock,payload,window['offset'],rid,row['source'],self.store.root,window,
                    resident=self.resident,confirmed=window['confirmed'],context_enabled=config['context_enabled'],terms=config['terms'],continuation={'key':rid+':'+str(round(window['offset']*RATE)),'enabled':config.get('prefix_enabled',True) and config['responsive'],
                    'confirmed':window['confirmed'],'previous_text':previous,'stable_text':stable})
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
                decoder=result.get('decoder',{})
                if decoder:
                    for key,hit in [('prefix_rounds',decoder.get('mode')=='prefix'),('feature_hits',decoder.get('feature_cache_hit',False)),('fallbacks',decoder.get('fallback',False))]:
                        totals[key]=totals.get(key,0)+int(bool(hit))
                metrics['continuation_break_at']=break_at
                metrics['decoder_totals']=dict(totals);result['decoder_totals']=dict(totals)
                metrics['preview_step_seconds']=preview_step(result['processing_ms'])
                if result['confirmed']:
                    self.checkpoint(rid,cursor=end,partial={},metrics=metrics)
                    self.emit(rid,'final',result=result);previous='';stable='';preview_end=0;preview_start=None;next_target=3
                else:
                    partial={'start':window['offset'],'end':window['next_offset'],'text':text,'stable_text':stable,**{k:window[k] for k in ('continuation_id','continues_previous','continues_next','boundary_reason')}}
                    self.checkpoint(rid,partial=partial,metrics=metrics)
                    self.emit(rid,'partial',partial=partial,metrics=metrics)
                    next_target=min(HARD_SECONDS,math.floor(window['next_offset']-window['offset'])+preview_step(result['processing_ms']))
        except asyncio.CancelledError:raise
        except (OSError,ValueError) as error:
            self.emit(rid,'status',message='实时会话暂时中断，原音与已确认文字保留：'+str(error)[:200])
        except Exception:
            self.emit(rid,'status',message='实时会话暂时中断，原音与已确认文字保留，可重新连接')
        finally:
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
            if self.row(rid)['status']=='stopped':yield encode({'type':'ended','seq':after})+'\n';return
            try:await asyncio.wait_for(wake.wait(),10)
            except asyncio.TimeoutError:yield encode({'type':'heartbeat','seq':after})+'\n'
