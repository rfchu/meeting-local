"""One read-only projection for capture, durable files and confirmed live ASR."""
import json,time
from .live_results import plan_ranges,RATE


def project(health,session,live,ranges,last_events,write=None):
    published=health['state']=='completed';native=health['source']=='native'
    raw=health['state'];age=health['metadata_age_seconds']
    capture='stopped' if published else 'interrupted' if raw in ('failed','interrupted') else 'unknown' if raw=='unknown' else raw
    if not published and native and health['capture']['heartbeat_stale'] and health['native_running']:capture='unknown'
    if not published and not native and age>90 and raw in ('recording','paused','starting'):capture='unknown'
    if capture not in ('starting','recording','paused','unknown','interrupted','stopped'):capture='unknown'
    audio=health['audio'];saved=audio['saved_bytes']>0
    storage='saved' if published and saved else 'missing' if published else 'partial' if audio['missing_chunk_count'] else 'writing' if saved and capture in ('recording','paused','starting') else 'available' if saved else 'waiting'
    if native and not published and capture=='recording' and any(t.get('file_state')=='unconfirmed' for t in health['capture'].get('tracks',[])):storage='unconfirmed'
    # A failed write stays visible until the next explicitly successful write.
    if (write=='storage.failed' or health.get('capture_failure_code')=='storage_failed') and not published:storage='failed'
    engine=session.get('draft_engine')
    if live:
        try:engine=json.loads(live['config']).get('engine',engine)
        except ValueError:pass
    selected,gaps=plan_ranges([r for r in ranges if r['state']=='completed' and r['engine']==engine],health['duration_seconds'])
    covered=round(sum((r['end_frame']-r['start_frame'])/RATE for r in selected),2)
    attempts=[r for r in ranges if r['engine']==engine]
    latest=attempts[-1]['state'] if attempts else None
    recognition='disabled' if not engine else 'failed' if latest=='failed' else 'stopped' if live and live['status']=='stopped' else 'running' if latest=='running' else 'behind' if health['duration_seconds']-covered>30 and live and live['status'] in ('running','draining') else 'waiting'
    phase='saved' if published and saved else 'interrupted' if published else capture
    if capture=='stopped' and not published:phase='saving' if any(e['code']=='capture.stop_requested' for e in last_events) else 'interrupted'
    end=health['duration_seconds'] if published and saved else max((t['saved_seconds'] for t in audio['tracks']),default=None)
    return {'phase':phase,'capture':{'state':capture,'heartbeat_age_seconds':health['capture']['heartbeat_age_seconds'],'tracks':health['capture'].get('tracks',[])},
            'storage':{'state':storage,'saved_bytes':audio['saved_bytes'],'last_saved_seconds':end,'saved_chunks':audio['saved_chunks'],'missing_chunks':audio['missing_chunk_count']},
            'asr':{'state':recognition,'engine':engine,'covered_seconds':covered,'gap_count':len(gaps) if engine else 0},
            'observed_at':time.time()}


class RecordingRuntime:
    def __init__(self,store,recordings,native,health,journal):
        self.store=store;self.recordings=recordings;self.native=native;self.health=health;self.journal=journal

    def read(self,rid,health=None):
        session=self.recordings.read(rid)
        live=self.store.one('SELECT config,status FROM live_sessions WHERE recording_id=?',(rid,))
        ranges=self.store.all('SELECT engine,start_frame,end_frame,state FROM live_asr_ranges WHERE recording_id=? ORDER BY rowid',(rid,))
        return project(health or self.health.snapshot(rid),session,live,ranges,self.journal.events(rid),self.journal.write_state(rid))

    def observe(self):
        # Observe only active or recently synchronized sessions; historical views stay read-only.
        candidates=set(self.native.processes)
        for path in self.recordings.root.glob('*/session.json'):
            if time.time()-path.stat().st_mtime<120:candidates.add(path.parent.name)
        for rid in candidates:
            try:
                value=self.read(rid)
                self.journal.transition(rid,'capture',value['capture']['state'],{'heartbeat_age_seconds':value['capture']['heartbeat_age_seconds']})
                for track in value['capture']['tracks']:
                    self.journal.transition(rid,track['name']+'_input',track['state'],{'saved_seconds':track['saved_seconds']})
                self.journal.transition(rid,'storage',value['storage']['state'],{'saved_bytes':value['storage']['saved_bytes'],'saved_seconds':value['storage']['last_saved_seconds'],'saved_chunks':value['storage']['saved_chunks'],'missing_chunks':value['storage']['missing_chunks']})
                self.journal.transition(rid,'asr',value['asr']['state'],{'covered_seconds':value['asr']['covered_seconds']})
            except (OSError,ValueError):continue
