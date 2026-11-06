"""Bounded operational journal. No audio, text, paths, prompts or raw errors."""
import fcntl,json,math,os,sqlite3,threading,time,uuid

COMPONENTS={'capture','storage','asr','system_input','microphone_input'}
STATES={'starting','recording','paused','unknown','interrupted','stopping','stopped','waiting','writing','available','partial','saved','missing','failed','running','behind','disabled','receiving','quiet','no_data','inactive','stalled'}
CODES={'page.detached','page.returned','service.started','service.shutdown_requested','service.clean_shutdown','service.shutdown_failed','service.previous_unclosed',
       'recording.created','capture.started','capture.paused','capture.resumed','capture.stop_requested','capture.exited','capture.reconciled','storage.completed','storage.failed'}
METRICS={'pid','exit_code','saved_bytes','saved_seconds','duration_seconds','saved_chunks','missing_chunks','covered_seconds','heartbeat_age_seconds','previous_last_seen'}


def metrics_only(values):
    return {k:v for k,v in values.items() if k in METRICS and (v is None or type(v) in (bool,int,float) and math.isfinite(v))}


class Lifecycle:
    def __init__(self,root):
        self.root=root;self.path=root/'lifecycle.sqlite3';self.run_id=None;self.available=True
        self.lock=threading.RLock();self.stop=threading.Event();self.thread=None;self.lease=None;self.observer=None
        try:
            with self.connect() as db:
                db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,pid INTEGER,started REAL,last_seen REAL,ended REAL,end_kind TEXT);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,recording_id TEXT,at REAL,code TEXT,metrics TEXT);
                CREATE INDEX IF NOT EXISTS recording_events ON events(recording_id,seq);
                CREATE TABLE IF NOT EXISTS writes(recording_id TEXT PRIMARY KEY,state TEXT);
                CREATE TABLE IF NOT EXISTS observations(recording_id TEXT,component TEXT,state TEXT,at REAL,metrics TEXT,PRIMARY KEY(recording_id,component));''')
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
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?) ON CONFLICT(recording_id,component) DO UPDATE SET state=excluded.state,at=excluded.at,metrics=excluded.metrics',(rid,component,state,time.time(),json.dumps(metrics_only(metrics))))
        except (OSError,sqlite3.Error):self.available=False

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
        self.lease=(self.root/'service.lock').open('a')
        try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.lease.close();self.lease=None;raise RuntimeError('已有服务持有此数据目录，未启动第二个工作进程')
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
        def loop():
            while not self.stop.wait(5):
                try:
                    if self.available:
                        with self.connect() as db:db.execute('UPDATE runs SET last_seen=? WHERE id=?',(time.time(),self.run_id))
                    if self.observer:self.observer()
                except (OSError,ValueError,sqlite3.Error):pass
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()

    def close(self,clean=True):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        self.record('service.clean_shutdown' if clean else 'service.shutdown_failed')
        if clean and self.available:
            try:
                with self.connect() as db:db.execute("UPDATE runs SET ended=?,end_kind='clean' WHERE id=?",(time.time(),self.run_id))
            except (OSError,sqlite3.Error):pass
        if self.lease:self.lease.close();self.lease=None
