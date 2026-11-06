"""Bounded operational journal. No audio, text, paths, prompts or raw errors."""
from .maintenance import DataLease
import fcntl,json,math,os,sqlite3,threading,time,uuid

COMPONENTS={'capture','storage','asr','system_input','microphone_input'}
STATES={'starting','recording','paused','unknown','interrupted','stopping','stopped','waiting','writing','available','partial','saved','missing','failed','running','behind','disabled','receiving','quiet','no_data','inactive','stalled','unconfirmed'}
CODES={'service.observation_gap','page.detached','page.returned','service.started','service.shutdown_requested','service.clean_shutdown','service.shutdown_failed','service.previous_unclosed',
       'recording.created','capture.started','capture.paused','capture.resumed','capture.stop_requested','capture.exited','capture.reconciled','storage.completed','storage.failed'}
ANOMALIES={'capture':{'unknown','interrupted','failed'},'storage':{'unconfirmed','failed'},
           'system_input':{'no_data','stalled'},'microphone_input':{'no_data','stalled'}}

METRICS={'pid','exit_code','saved_bytes','saved_seconds','duration_seconds','saved_chunks','missing_chunks','covered_seconds','heartbeat_age_seconds','previous_last_seen','gap_seconds'}


def metrics_only(values):
    return {k:v for k,v in values.items() if k in METRICS and (v is None or type(v) in (bool,int,float) and math.isfinite(v))}


class Lifecycle:
    def __init__(self,root):
        self.root=root;self.path=root/'lifecycle.sqlite3';self.run_id=None;self.available=True
        self.lock=threading.RLock();self.stop=threading.Event();self.thread=None;self.lease=None;self.maintenance=None;self.observer=None;self.last_observed=None
        try:
            with self.connect() as db:
                db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,pid INTEGER,started REAL,last_seen REAL,ended REAL,end_kind TEXT);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,recording_id TEXT,at REAL,code TEXT,metrics TEXT);
                CREATE INDEX IF NOT EXISTS recording_events ON events(recording_id,seq);
                CREATE TABLE IF NOT EXISTS writes(recording_id TEXT PRIMARY KEY,state TEXT);
                CREATE TABLE IF NOT EXISTS observations(recording_id TEXT,component TEXT,state TEXT,at REAL,metrics TEXT,PRIMARY KEY(recording_id,component));
                CREATE TABLE IF NOT EXISTS anomalies(id INTEGER PRIMARY KEY AUTOINCREMENT,recording_id TEXT,component TEXT,state TEXT,started REAL,last_seen REAL,ended REAL,start_seconds REAL,last_seconds REAL,end_state TEXT);
                CREATE INDEX IF NOT EXISTS recording_anomalies ON anomalies(recording_id,id);
                CREATE TABLE IF NOT EXISTS anomaly_totals(recording_id TEXT PRIMARY KEY,total INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS quality_monitors(recording_id TEXT PRIMARY KEY,started REAL);
                CREATE TABLE IF NOT EXISTS anomaly_reviews(anomaly_id INTEGER PRIMARY KEY,recording_id TEXT,status TEXT,version INTEGER,updated REAL);''')
        except (OSError,sqlite3.Error):self.available=False

    def connect(self):return sqlite3.connect(self.path,timeout=.2)

    def record(self,code,rid=None,**metrics):
        if code not in CODES and not (code.split('.')[0] in COMPONENTS and code.split('.')[-1] in STATES):raise ValueError('未知生命周期事件')
        if rid is not None and (len(rid)!=32 or any(c not in '0123456789abcdef' for c in rid)):return False
        if not self.available:return False
        try:
            with self.lock,self.connect() as db:
                if code in ('storage.available','storage.failed','storage.completed'):
                    old=db.execute('SELECT state FROM writes WHERE recording_id=?',(rid,)).fetchone()
                    if old and old[0]==code:return True
                    db.execute('INSERT INTO writes VALUES(?,?) ON CONFLICT(recording_id) DO UPDATE SET state=excluded.state',(rid,code))
                db.execute('INSERT INTO events(run_id,recording_id,at,code,metrics) VALUES(?,?,?,?,?)',(self.run_id,rid,time.time(),code,json.dumps(metrics_only(metrics))))
                component,state=code.split('.',1)
                if rid and component in COMPONENTS and state in STATES:
                    db.execute('INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(recording_id,component) DO UPDATE SET state=excluded.state,at=excluded.at,metrics=excluded.metrics',(rid,component,state,time.time(),json.dumps(metrics_only(metrics))))
                if rid and component in ANOMALIES:self._track_anomaly(db,rid,component,state,metrics)
                db.execute('DELETE FROM events WHERE seq <= (SELECT MAX(seq)-10000 FROM events)')
            return True
        except (OSError,sqlite3.Error):self.available=False;return False

    def write_state(self,rid):
        if not self.available:return None
        try:
            with self.lock,self.connect() as db:row=db.execute('SELECT state FROM writes WHERE recording_id=?',(rid,)).fetchone()
            return row[0] if row else None
        except (OSError,sqlite3.Error):return None

    def transition(self,rid,component,state,metrics):
        if component not in COMPONENTS or state not in STATES:raise ValueError('未知运行状态')
        if not self.available:return
        try:
            with self.lock,self.connect() as db:
                old=db.execute('SELECT state FROM observations WHERE recording_id=? AND component=?',(rid,component)).fetchone()
                if not old or old[0]!=state:self.record(component+'.'+state,rid,**metrics)
                self._track_anomaly(db,rid,component,state,metrics)
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(recording_id,component) DO UPDATE SET state=excluded.state,at=excluded.at,metrics=excluded.metrics',(rid,component,state,time.time(),json.dumps(metrics_only(metrics))))
        except (OSError,sqlite3.Error):self.available=False

    def _track_anomaly(self,db,rid,component,state,metrics):
        if component not in ANOMALIES:return
        if component in ('capture','system_input','microphone_input') and state in {'recording','paused','unknown','interrupted','failed','receiving','quiet','no_data','stalled'}:
            db.execute('INSERT OR IGNORE INTO quality_monitors VALUES(?,?)',(rid,time.time()))
        now=time.time();seconds=metrics_only(metrics).get('duration_seconds')
        if seconds is not None:seconds=max(0,seconds)
        current=db.execute('SELECT id,state FROM anomalies WHERE recording_id=? AND component=? AND ended IS NULL',(rid,component)).fetchone()
        if current and current[1]==state:
            db.execute('UPDATE anomalies SET last_seen=?,last_seconds=COALESCE(?,last_seconds) WHERE id=?',(now,seconds,current[0]))
            return
        if current:
            db.execute('UPDATE anomalies SET ended=?,last_seen=?,last_seconds=COALESCE(?,last_seconds),end_state=? WHERE id=?',(now,now,seconds,state,current[0]))
        if state in ANOMALIES[component]:
            db.execute('INSERT INTO anomalies(recording_id,component,state,started,last_seen,start_seconds,last_seconds) VALUES(?,?,?,?,?,?,?)',(rid,component,state,now,now,seconds,seconds))
            db.execute('INSERT INTO anomaly_totals VALUES(?,1) ON CONFLICT(recording_id) DO UPDATE SET total=total+1',(rid,))
            # Keep recent automatic details; open observations and manual review history survive pruning.
            db.execute('DELETE FROM anomalies WHERE recording_id=? AND ended IS NOT NULL AND id NOT IN (SELECT anomaly_id FROM anomaly_reviews) AND id NOT IN (SELECT id FROM anomalies WHERE recording_id=? ORDER BY id DESC LIMIT 200)',(rid,rid))

    def review_anomaly(self,rid,aid,status,expected_version):
        if status not in {'pending','clear','gap','uncertain'}:raise ValueError('未知核对结论')
        if not self.available:raise RuntimeError('录音异常记录暂不可用')
        try:
            with self.lock,self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                item=db.execute('SELECT ended FROM anomalies WHERE id=? AND recording_id=?',(aid,rid)).fetchone()
                if not item:raise LookupError('异常详情不存在或已超出保留范围')
                if item[0] is None:raise ValueError('异常状态仍在变化，请在状态结束后核对')
                old=db.execute('SELECT status,version FROM anomaly_reviews WHERE anomaly_id=?',(aid,)).fetchone()
                previous,version=old if old else ('pending',0)
                # Retrying the same choice after a lost response is idempotent.
                if previous==status:return
                if version!=expected_version:raise ValueError('核对结果已在其他页面更新，请刷新后重试')
                db.execute('INSERT INTO anomaly_reviews VALUES(?,?,?,?,?) ON CONFLICT(anomaly_id) DO UPDATE SET status=excluded.status,version=excluded.version,updated=excluded.updated',(aid,rid,status,version+1,time.time()))
        except (OSError,sqlite3.Error) as error:raise RuntimeError('核对结果保存失败，请重试') from error

    def anomalies(self,rid,limit=20,*,pending_only=False,offset=0,gaps_only=False):
        unavailable={'available':False,'monitored':False,'total_count':0,'items':[]}
        if not self.available:return unavailable
        try:
            with self.lock,self.connect() as db:
                monitored=db.execute('SELECT started FROM quality_monitors WHERE recording_id=?',(rid,)).fetchone()
                count=db.execute('SELECT total FROM anomaly_totals WHERE recording_id=?',(rid,)).fetchone()
                joined="FROM anomalies a LEFT JOIN anomaly_reviews r ON r.anomaly_id=a.id WHERE a.recording_id=?"
                retained,reviewed,gaps=db.execute("SELECT COUNT(*),COALESCE(SUM(COALESCE(r.status,'pending') IN ('clear','gap')),0),COALESCE(SUM(r.status='gap'),0) "+joined,(rid,)).fetchone()
                clause=" AND r.status='gap'" if gaps_only else " AND COALESCE(r.status,'pending') IN ('pending','uncertain')" if pending_only else ''
                rows=db.execute("SELECT a.id,a.component,a.state,a.started,a.last_seen,a.ended,a.start_seconds,a.last_seconds,a.end_state,COALESCE(r.status,'pending'),COALESCE(r.version,0),r.updated "+joined+clause+' ORDER BY a.id DESC LIMIT ? OFFSET ?',(rid,limit,offset)).fetchall()
            return {'available':True,'monitored':bool(monitored),'total_count':count[0] if count else 0,
                    'retained_count':retained,'reviewed_count':reviewed,'pending_count':retained-reviewed,'gap_count':gaps,
                    'offset':offset,'pending_only':pending_only,'items':[
                dict(zip(('id','component','state','observed_at','last_observed_at','closed_at','start_seconds','end_seconds','end_state','review_status','review_version','reviewed_at'),row)) for row in rows]}
        except (OSError,sqlite3.Error):return unavailable

    def events(self,rid=None,limit=50):
        if not self.available:return []
        try:
            with self.lock,self.connect() as db:
                rows=db.execute('SELECT seq,at,code,metrics FROM events WHERE recording_id IS ? ORDER BY seq DESC LIMIT ?',(rid,limit)).fetchall()
            return [{'seq':r[0],'at':r[1],'code':r[2],'metrics':json.loads(r[3])} for r in rows]
        except (OSError,sqlite3.Error):return []

    def checkpoints(self,rid):
        if not self.available:return {}
        try:
            with self.lock,self.connect() as db:rows=db.execute('SELECT component,state,at,metrics FROM observations WHERE recording_id=?',(rid,)).fetchall()
            return {r[0]:{'state':r[1],'at':r[2],'metrics':json.loads(r[3])} for r in rows}
        except (OSError,sqlite3.Error):return {}

    def start(self):
        self.maintenance=DataLease(self.root).acquire()
        try:
            self.lease=(self.root/'service.lock').open('a')
            fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            if self.lease:self.lease.close();self.lease=None
            self.maintenance.close();self.maintenance=None
            raise RuntimeError('已有服务持有此数据目录，未启动第二个工作进程') from None
        self.run_id=uuid.uuid4().hex
        if self.available:
            try:
                with self.connect() as db:
                    for previous, in db.execute('SELECT last_seen FROM runs WHERE end_kind IS NULL'):
                        self.record('service.previous_unclosed',previous_last_seen=previous)
                    db.execute("UPDATE runs SET end_kind='unclosed' WHERE end_kind IS NULL")
                    db.execute('INSERT INTO runs(id,pid,started,last_seen) VALUES(?,?,?,?)',(self.run_id,os.getpid(),time.time(),time.time()))
                    db.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY started DESC LIMIT 100)')
            except (OSError,sqlite3.Error):self.available=False
        self.record('service.started',pid=os.getpid())
        self.last_observed=time.monotonic()
        def loop():
            while not self.stop.wait(5):self.observe_once()
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()

    def observe_once(self):
        now=time.monotonic()
        if self.last_observed is not None and now-self.last_observed>20:
            # A scheduling gap alone cannot establish sleep, device loss or a crash.
            self.record('service.observation_gap',gap_seconds=round(now-self.last_observed,1))
        self.last_observed=now
        try:
            if self.available:
                with self.connect() as db:db.execute('UPDATE runs SET last_seen=? WHERE id=?',(time.time(),self.run_id))
            if self.observer:self.observer()
        except (OSError,ValueError,sqlite3.Error):pass

    def close(self,clean=True):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        self.record('service.clean_shutdown' if clean else 'service.shutdown_failed')
        if clean and self.available:
            try:
                with self.connect() as db:db.execute("UPDATE runs SET ended=?,end_kind='clean' WHERE id=?",(time.time(),self.run_id))
            except (OSError,sqlite3.Error):pass
        if self.lease:self.lease.close();self.lease=None
        if self.maintenance:self.maintenance.close();self.maintenance=None
