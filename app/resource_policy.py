"""Observe managed live demand, including unconfirmed pause tails, without model/device calls."""
import json,re,math


def sync_live_demand(scheduler,store):
    owners=[]
    for row in store.all("SELECT recording_id,source,status,cursor FROM live_sessions WHERE status IN ('running','paused','draining')"):
        rid=row['recording_id']
        if row['status']!='draining':
            if row['source']=='native' and re.fullmatch('[0-9a-f]{32}',rid):
                try:
                    status=json.loads((store.root/'recordings'/rid/'capture.json').read_text())
                    duration=status.get('duration')
                    if status.get('state')=='paused' and type(duration) in (int,float) and duration>=0 and math.isfinite(duration) and duration*16000<=row['cursor']:continue
                except (OSError,ValueError,TypeError,AttributeError,OverflowError):pass
            elif row['status']=='paused':
                end=store.one('SELECT MAX(end_frame) AS last FROM live_pcm_inputs WHERE recording_id=?',(rid,))['last'] or 0
                if end<=row['cursor']:continue
        owners.append(rid)
    scheduler.set_realtime(owners)
