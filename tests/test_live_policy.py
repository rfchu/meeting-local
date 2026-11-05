import asyncio,json
from app.live_policy import sentence_hint,update_load
from app.live_segmentation import elastic_plan,RATE
from app.live_session import pcm_wav
from test_live_session import setup,until,audio,CONFIG

VOICE=b'\x70\x17'
def raw(seconds):return VOICE*round(seconds*RATE)

def test_sentence_hint_requires_real_pause_and_proximity():
    assert sentence_hint('说完了。”',5)==5
    assert sentence_hint('还没说完，',5) is None
    assert sentence_hint('版本 1.5',5) is None
    assert elastic_plan(raw(5.4),7,sentence_end=5) is None
    quiet=b'\0\0'*round(.4*RATE)
    plan=elastic_plan(raw(5)+quiet,7,sentence_end=5)
    assert plan['confirmed'] and plan['count']==round(5.4*RATE)
    assert plan['boundary_reason']=='sentence_pause'
    assert elastic_plan(raw(5)+quiet,7,sentence_end=3) is None
    assert elastic_plan(raw(5)+quiet,7) is None
    assert elastic_plan(raw(5)+b'\0\0'*round(.8*RATE),7)['boundary_reason']=='silence'

def test_load_hysteresis_and_bounded_cadence():
    state=update_load({},10000,5,0)
    assert state['mode']=='balanced'
    state=update_load(state,10000,5,0)
    assert state['mode']=='saving' and 6<=state['preview_step_seconds']<=10
    first=update_load(state,100,10,0)
    assert first['mode']=='saving'
    for _ in range(20):state=update_load(state,100,10,0)
    assert state['mode']=='balanced' and state['preview_step_seconds']==2
    for _ in range(2):state=update_load(state,100,10,30)
    assert state['mode']=='saving' and state['backlog_seconds']==30
    assert update_load(state,0,0,-5)['backlog_seconds']==0

def test_short_sentence_pause_commits_contiguous_audio_without_waiting_preview_target(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            manager.ingest(rid,0,audio(5))
            await until(lambda:bool(json.loads(manager.row(rid)['partial'])))
            assert manager.row(rid)['cursor']==0
            manager.ingest(rid,5,pcm_wav(b'\0\0'*round(.5*RATE)))
            await until(lambda:manager.row(rid)['cursor']>0)
            assert manager.row(rid)['cursor']==round(5.4*RATE)
            snap=manager.snapshot(rid)
            assert snap['metrics']['boundary_reason']=='sentence_pause'
            assert snap['metrics']['load']['mode']=='balanced'
            assert manager.cache.overview(rid,'qwen',5.4)['gaps']==[]
        finally:await manager.close()
    asyncio.run(run())

def test_busy_asr_waits_without_creating_failed_ranges_and_resumes(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        lock.acquire()
        try:
            await manager.start(rid,CONFIG);manager.ingest(rid,0,audio(5))
            await until(lambda:bool(manager.store.one('SELECT seq FROM live_sessions WHERE recording_id=? AND seq>0',(rid,))))
            assert resident.calls==[]
            assert not manager.store.all('SELECT id FROM live_asr_ranges WHERE recording_id=?',(rid,))
            lock.release()
            await until(lambda:bool(json.loads(manager.row(rid)['partial'])))
            assert resident.calls==[5]
        finally:
            if lock.locked():lock.release()
            await manager.close()
    asyncio.run(run())
