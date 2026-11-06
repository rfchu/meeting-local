"""Opt-in observation of existing native recordings. Never starts or controls capture."""
import json,sqlite3,threading,time,uuid
from .capture_diagnostics import sanitize,observe
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from typing import Literal

MARKERS=('background_return','device_changed','other_app_changed','pause_resume','wake_return')


class ValidationStart(BaseModel):
    recording_id:str=Field(min_length=32,max_length=32,pattern='^[0-9a-f]+$')
    target_seconds:int=Field(default=10800,ge=60,le=43200)


class ValidationMarker(BaseModel):
    kind:Literal['background_return','device_changed','other_app_changed','pause_resume','wake_return']


class RecordingValidation:
    def __init__(self,root,recordings,native,runtime,*,wall=None,monotonic=None):
        self.path=root/'recording-validation.sqlite3';self.recordings=recordings;self.native=native;self.runtime=runtime
        self.wall=wall or time.time;self.monotonic=monotonic or time.monotonic
        self.lock=threading.RLock();self.closed=threading.Event();self.thread=None;self.starts={};self.ticks={};self.available=True
        try:
            with self.connect() as db:
                db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS checks(id TEXT PRIMARY KEY,rid TEXT,state TEXT,target INTEGER,started REAL,updated REAL,elapsed REAL,summary TEXT);
                CREATE TABLE IF NOT EXISTS samples(check_id TEXT,seq INTEGER,at REAL,elapsed REAL,payload TEXT,PRIMARY KEY(check_id,seq));
                CREATE TABLE IF NOT EXISTS markers(check_id TEXT,seq INTEGER,at REAL,elapsed REAL,kind TEXT,PRIMARY KEY(check_id,seq));''')
        except (OSError,sqlite3.Error):self.available=False

    def connect(self):
        db=sqlite3.connect(self.path,timeout=1);db.row_factory=sqlite3.Row;return db

    def recover(self):
        # Restart does not resume observation or imply that audio capture continued.
        with self.lock,self.connect() as db:
            for row in db.execute("SELECT * FROM checks WHERE state='observing'").fetchall():
                summary=json.loads(row['summary']);summary['end_reason']='service_restart'
                db.execute("UPDATE checks SET state='interrupted',updated=?,summary=? WHERE id=?",(self.wall(),json.dumps(summary),row['id']))

    def start_monitor(self):
        if not self.available:return
        try:self.recover()
        except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):self.available=False;return
        def loop():
            while not self.closed.wait(5):
                try:self.sample()
                except (OSError,ValueError,RuntimeError,sqlite3.Error,KeyError,TypeError):pass
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()

    def start(self,rid,target=10800):
        if not 60<=target<=43200:raise ValueError('观察时长须在一分钟到十二小时之间')
        with self.lock:
            if self.closed.is_set():raise ValueError('观察服务已关闭')
            session=self.recordings.read(rid)
            if session.get('source')!='macos' or session.get('state')=='completed' or not self.native.is_running(rid):raise ValueError('请先在录音工作台启动原生录音，再选择该录音观察')
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing=db.execute("SELECT id,rid,target FROM checks WHERE state='observing'").fetchone()
                if existing:
                    if existing['rid']==rid and existing['target']==target:return self.get(existing['id'])
                    raise ValueError('已有观察正在进行，请先停止该观察')
                cid=uuid.uuid4().hex;now=self.wall()
                db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?)',(cid,rid,'observing',target,now,now,0,json.dumps({'sample_count':0,'flags':{},'end_reason':None})))
                old=[r[0] for r in db.execute("SELECT id FROM checks WHERE state!='observing' ORDER BY started DESC LIMIT -1 OFFSET 19")]
                for key in old:
                    for table in ('samples','markers'):db.execute(f'DELETE FROM {table} WHERE check_id=?',(key,))
                    db.execute('DELETE FROM checks WHERE id=?',(key,))
            self.starts[cid]=self.monotonic();self.ticks[cid]=self.starts[cid]
            self.sample()
            return self.get(cid)

    def sample(self):
        with self.lock,self.connect() as db:
            rows=db.execute("SELECT * FROM checks WHERE state='observing'").fetchall()
            for row in rows:
                cid=row['id'];now=self.monotonic();elapsed=max(0,now-self.starts.get(cid,now))
                summary=json.loads(row['summary']);flags=[];state='observing'
                if cid not in self.starts:state='interrupted';summary['end_reason']='observer_not_attached'
                gap=max(0,now-self.ticks.get(cid,now))
                if gap>20:flags.append('observation_gap')
                try:
                    alive=self.native.is_running(row['rid'])
                    if not alive:state='interrupted';summary['end_reason']='capture_not_running';flags.append('capture_not_running')
                    view=self.runtime.read(row['rid'])
                    sample={'phase':view['phase'],'capture':view['capture']['state'],'storage':view['storage']['state'],
                            'asr':view['asr']['state'],'saved_bytes':view['storage']['saved_bytes'],
                            'saved_seconds':view['storage']['last_saved_seconds'],'covered_seconds':view['asr']['covered_seconds'],'timeline_seconds':view['capture'].get('timeline_seconds')}
                    sample['native_diagnostics']=sanitize(view['capture'].get('native_diagnostics'))
                    native_flags,native_delta=observe(summary,sample['native_diagnostics']);flags.extend(native_flags)
                    sample['native_counter_delta']=native_delta
                    previous_bytes=summary.get('last_sample',{}).get('saved_bytes')
                    sample['file_growth_bytes']=sample['saved_bytes']-previous_bytes if type(previous_bytes) is int and type(sample['saved_bytes']) is int else None
                    if sample['file_growth_bytes'] is not None and sample['file_growth_bytes']<0:flags.append('audio_file_shrank')
                    if alive and sample['capture'] in ('stopped','interrupted'):state='interrupted';summary['end_reason']='capture_ended';flags.append('capture_ended')
                    if sample['capture']=='unknown':flags.append('capture_unknown')
                    if sample['storage'] in ('unconfirmed','failed'):flags.append('storage_unconfirmed')
                    if sample['asr'] in ('failed','stopped'):flags.append('asr_attention')
                    if sample['capture']=='recording' and sample['asr'] not in ('disabled','failed','stopped') and (sample['timeline_seconds'] or 0)-sample['covered_seconds']>60:flags.append('asr_lag_observed')
                    for track in view['capture'].get('tracks',[]):
                        if track['name'] in ('system','microphone') and track['state']=='no_data':flags.append(track['name']+'_no_data')
                except (OSError,ValueError,RuntimeError,sqlite3.Error,KeyError,TypeError):
                    sample={'phase':'unknown','capture':'unknown','storage':'unknown','asr':'unknown','saved_bytes':None,'saved_seconds':None,'covered_seconds':None}
                    flags.append('status_unavailable')
                if state=='observing' and elapsed>=row['target']:state='completed';summary['end_reason']='target_elapsed'
                flags=list(dict.fromkeys(flags))
                summary['sample_count']+=1
                for flag in flags:summary['flags'][flag]=summary['flags'].get(flag,0)+1
                summary['last_sample']=sample;summary['max_observation_gap_seconds']=round(max(gap,summary.get('max_observation_gap_seconds',0)),2)
                db.execute('INSERT INTO samples VALUES(?,?,?,?,?)',(cid,summary['sample_count'],self.wall(),elapsed,json.dumps(sample|{'flags':flags})))
                db.execute('DELETE FROM samples WHERE check_id=? AND seq<=?',(cid,summary['sample_count']-9000))
                db.execute('UPDATE checks SET state=?,updated=?,elapsed=?,summary=? WHERE id=?',(state,self.wall(),elapsed,json.dumps(summary),cid))
                db.commit()
                if state!='observing':self.starts.pop(cid,None);self.ticks.pop(cid,None)
                else:self.ticks[cid]=now

    def get(self,cid):
        with self.lock,self.connect() as db:
            row=db.execute('SELECT * FROM checks WHERE id=?',(cid,)).fetchone()
            markers=db.execute('SELECT COUNT(*) FROM markers WHERE check_id=?',(cid,)).fetchone()[0]
        if not row:raise LookupError('观察记录不存在或已超出保留范围')
        return {'id':row['id'],'recording_id':row['rid'],'state':row['state'],'target_seconds':row['target'],
                'marker_count':markers,'started_at':row['started'],'updated_at':row['updated'],'status_fresh':row['state']!='observing' or max(0,self.wall()-row['updated'])<=20,'elapsed_seconds':round(row['elapsed'],2),**json.loads(row['summary'])}

    def list(self):
        with self.lock,self.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM checks ORDER BY started DESC LIMIT 20')]
        return [self.get(cid) for cid in ids]

    def stop(self,cid):
        with self.lock,self.connect() as db:
            row=self.get(cid)
            if row['state']=='observing':
                summary=json.loads(db.execute('SELECT summary FROM checks WHERE id=?',(cid,)).fetchone()[0]);summary['end_reason']='user_stopped_observation'
                db.execute("UPDATE checks SET state='stopped',updated=?,summary=? WHERE id=?",(self.wall(),json.dumps(summary),cid))
                self.starts.pop(cid,None);self.ticks.pop(cid,None)
        return self.get(cid)

    def mark(self,cid,kind):
        if kind not in MARKERS:raise ValueError('未知人工标记')
        with self.lock,self.connect() as db:
            row=self.get(cid)
            if row['state']!='observing':raise ValueError('观察已结束，不能追加现场标记')
            count=db.execute('SELECT COUNT(*) FROM markers WHERE check_id=?',(cid,)).fetchone()[0]
            if count>=200:raise ValueError('本次人工标记已达上限')
            db.execute('INSERT INTO markers VALUES(?,?,?,?,?)',(cid,count+1,self.wall(),max(0,self.monotonic()-self.starts.get(cid,self.monotonic())),kind))
        return {'ok':True}

    def report(self,cid):
        with self.lock,self.connect() as db:
            row=self.get(cid);row.pop('recording_id');row.pop('id')
            samples=[{'seq':r['seq'],'at':r['at'],'elapsed_seconds':r['elapsed'],**json.loads(r['payload'])} for r in db.execute('SELECT * FROM samples WHERE check_id=? ORDER BY seq',(cid,))]
            markers=[{k:r[k] for k in ('seq','at','elapsed','kind')} for r in db.execute('SELECT * FROM markers WHERE check_id=? ORDER BY seq',(cid,))]
        return {'schema_version':2,'mode':'existing_native_recording_observation','result':'manual_review_required',
                'native_diagnostics_available':bool(row.get('last_sample',{}).get('native_diagnostics',{}).get('available')),
                'target_elapsed':row['state']=='completed','hardware_capture_validated':False,'audio_content_validated':False,'sleep_wake_validated':False,
                'other_app_contention_validated':False,'markers_are_user_reported':True,**row,
                'retained_sample_count':len(samples),'samples_truncated':len(samples)<row['sample_count'],'samples':samples,'markers':markers}

    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(timeout=2)
        try:
            with self.lock,self.connect() as db:
                for row in db.execute("SELECT * FROM checks WHERE state='observing'").fetchall():
                    summary=json.loads(row['summary']);summary['end_reason']='service_shutdown'
                    db.execute("UPDATE checks SET state='interrupted',updated=?,summary=? WHERE id=?",(self.wall(),json.dumps(summary),row['id']))
        except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):self.available=False
        finally:self.starts.clear();self.ticks.clear()


def register_recording_validation(app,checks):
    def call(fn,*args):
        try:return fn(*args)
        except LookupError as error:raise HTTPException(404,str(error))
        except (FileNotFoundError,ValueError) as error:raise HTTPException(409,str(error))
        except (OSError,sqlite3.Error):raise HTTPException(503,'观察记录暂不可用，录音采集不受此观察工具控制')
    @app.get('/api/runtime/recording-checks')
    def listing():return call(checks.list)
    @app.post('/api/runtime/recording-checks',status_code=201)
    def start(body:ValidationStart):return call(checks.start,body.recording_id,body.target_seconds)
    @app.post('/api/runtime/recording-checks/{cid}/stop')
    def stop(cid:str):return call(checks.stop,cid)
    @app.post('/api/runtime/recording-checks/{cid}/markers')
    def mark(cid:str,body:ValidationMarker):return call(checks.mark,cid,body.kind)
    @app.get('/api/runtime/recording-checks/{cid}/report')
    def report(cid:str):return JSONResponse(call(checks.report,cid),headers={'Content-Disposition':'attachment; filename="recording-observation.json"','Cache-Control':'no-store'})
