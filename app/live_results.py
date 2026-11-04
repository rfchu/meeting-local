"""Durable, range-bound live ASR attempts and immutable fast-completion plans."""
import hashlib,json,math,time
from .db import uid,encode

RATE=16000

def plan_ranges(rows,duration):
    end=round(duration*RATE);cursor=0;reused=[];gaps=[]
    for row in sorted(rows,key=lambda r:(r['start_frame'],-r['end_frame'])):
        start=row['start_frame'];stop=min(row['end_frame'],end)
        if start<cursor or stop<=start or start>=end:continue
        if start>cursor:gaps.append((cursor/RATE,start/RATE))
        reused.append(row|{'end_frame':stop});cursor=stop
    if cursor<end:gaps.append((cursor/RATE,end/RATE))
    return reused,gaps

class LiveResults:
    def __init__(self,store,recordings):self.store=store;self.recordings=recordings
    def check_session(self,rid,source):
        session=self.recordings.read(rid)
        actual='native' if session.get('source')=='macos' else 'browser'
        if actual!=source:raise ValueError('草稿音源与录音会话不一致')
        if session['state']=='completed' and not self.store.one('SELECT id FROM meetings WHERE id=?',(rid,)):
            raise ValueError('会议已删除，不能重新保存实时结果')
        return session
    def begin(self,rid,source,engine,offset,duration,payload,identity):
        with self.recordings.lock:
            session=self.check_session(rid,source)
            session['draft_engine']=engine
            self.recordings.write(self.recordings.folder(rid)/'session.json',session)
        start=round(offset*RATE);end=start+round(duration*RATE)
        digest=hashlib.sha256(payload).hexdigest();identity=encode(identity)
        if start<0 or end<=start:raise ValueError('实时识别范围无效')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            cached=db.execute("SELECT * FROM live_asr_ranges WHERE recording_id=? AND source=? AND engine=? AND start_frame=? AND end_frame=? AND audio_hash=? AND identity=? AND state='completed' ORDER BY rowid DESC LIMIT 1",(rid,source,engine,start,end,digest,identity)).fetchone()
            if cached:return None,[dict(s,live_range_id=cached['id'],live_segment_index=i) for i,s in enumerate(json.loads(cached['segments']))]
            aid=uid()
            db.execute('INSERT INTO live_asr_ranges(id,recording_id,source,engine,start_frame,end_frame,audio_hash,identity,state) VALUES(?,?,?,?,?,?,?,?,?)',
                       (aid,rid,source,engine,start,end,digest,identity,'running'))
        return aid,None
    def complete(self,aid,segments):
        self.store.execute("UPDATE live_asr_ranges SET state='completed',segments=?,error='',updated=CURRENT_TIMESTAMP WHERE id=?",(encode(segments),aid))
    def preview(self,aid):
        self.store.execute("UPDATE live_asr_ranges SET state='preview',error='',updated=CURRENT_TIMESTAMP WHERE id=?",(aid,))
    def fail(self,aid,error):
        self.store.execute("UPDATE live_asr_ranges SET state='failed',error=?,updated=CURRENT_TIMESTAMP WHERE id=? AND state='running'",(str(error)[:1000],aid))
    def snapshot(self,rid,engine):
        rows=self.store.all("SELECT * FROM live_asr_ranges WHERE recording_id=? AND engine=? AND state='completed' ORDER BY rowid DESC",(rid,engine))
        accepted={}
        for correction in self.store.all("SELECT * FROM live_corrections WHERE recording_id=? AND state='accepted'",(rid,)):
            accepted.setdefault(correction['range_id'],[]).append(correction)
        seen=set();result=[]
        for row in rows:
            key=(row['start_frame'],row['end_frame'])
            if key in seen:continue
            seen.add(key);row['segments']=json.loads(row['segments'])
            from .live_corrections import apply_edits
            for correction in accepted.get(row['id'],[]):
                i=correction['segment_index']
                if i<len(row['segments']) and row['segments'][i]['text']==correction['original_text']:
                    row['segments'][i]=dict(row['segments'][i],text=apply_edits(correction['original_text'],json.loads(correction['edits'])),asr_original_text=correction['original_text'],live_correction_id=correction['id'])
            result.append(row)
        return result
    def overview(self,rid,engine,duration=None):
        session=self.recordings.read(rid)
        if duration is None:
            duration=session['duration']
            if session.get('source')=='macos':
                try:duration=json.loads((self.recordings.folder(rid)/'capture.json').read_text()).get('duration',duration)
                except (FileNotFoundError,ValueError):pass
        rows=self.snapshot(rid,engine);reused,gaps=plan_ranges(rows,duration)
        states=self.store.all('SELECT state,COUNT(*) AS count FROM live_asr_ranges WHERE recording_id=? AND engine=? GROUP BY state',(rid,engine))
        attempts=self.store.all("SELECT id,start_frame,end_frame,state,error FROM live_asr_ranges WHERE recording_id=? AND engine=? AND state!='completed' ORDER BY rowid DESC LIMIT 50",(rid,engine))
        return {'engine':engine,'duration':duration,'covered_seconds':sum((r['end_frame']-r['start_frame'])/RATE for r in reused),
                'completed_ranges':len(reused),'gaps':[{'start':a,'end':b} for a,b in gaps],
                'attempts':{row['state']:row['count'] for row in states},
                'recent_failures':[{'start':r['start_frame']/RATE,'end':r['end_frame']/RATE,'state':r['state'],'error':r['error']} for r in attempts]}
