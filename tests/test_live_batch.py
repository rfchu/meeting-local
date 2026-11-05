import asyncio,json,sys,os
import pytest
from app.live_batch import whisper_events
from app.live_session import LiveSessions
from test_live_session import setup,until,audio,CONFIG

class Whisper:
    def ready(self):return True
    def identity(self):return {'synthetic':'whisper'}
    def live_command(self,audio,output):
        return [sys.executable,'-c',"import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('[{\"start\":0,\"end\":15,\"text\":\"合成转写结果\"}]')",str(output.with_suffix('.json'))]
    def read_live_result(self,output):return json.loads(output.with_suffix('.json').read_text())

WCONFIG={**CONFIG,'engine':'whisper'}

def test_whisper_is_backend_owned_and_snapshots_events_and_tail_are_durable(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path);manager.batch_provider=Whisper
        try:
            await manager.start(rid,WCONFIG)
            manager.ingest(rid,0,audio(15))
            await until(lambda:manager.row(rid)['cursor']==15*16000)
            assert not resident.calls
            snapshot=manager.snapshot(rid)
            assert snapshot['engine']=='whisper' and snapshot['protocol_version']==1
            assert snapshot['config']['responsive'] is False and snapshot['partial']=={}
            assert len(snapshot['segments'])==1 and snapshot['segments'][0]['live_range_id']
            assert manager.cache.overview(rid,'whisper',15)['covered_seconds']==15
            assert manager.cache.overview(rid,'qwen',15)['covered_seconds']==0
            events=[json.loads(e['payload']) for e in manager.store.all('SELECT payload FROM live_session_events WHERE recording_id=?',(rid,))]
            assert events and all(e['recording_id']==rid and e['engine']=='whisper' for e in events)
            manager.ingest(rid,15,audio(2))
            await manager.finish(rid,timeout=3)
            assert manager.row(rid)['cursor']==17*16000
            assert manager.snapshot(rid)['segments'][-1]['end']==17
            assert manager.cache.overview(rid,'whisper',17)['gaps']==[]
        finally:await manager.close()
    asyncio.run(run())

def test_whisper_restart_reuses_confirmed_ranges_and_cannot_switch_engine(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path);manager.batch_provider=Whisper
        await manager.start(rid,WCONFIG);manager.ingest(rid,0,audio(15))
        await until(lambda:manager.row(rid)['cursor']==15*16000)
        await manager.close()
        restarted=LiveSessions(manager.store,recordings,manager.native,manager.cache,lock,resident,manager.provider,Whisper)
        try:
            await restarted.resume()
            with pytest.raises(ValueError,match='不能更换'):await restarted.start(rid,CONFIG)
            assert restarted.snapshot(rid)['engine']=='whisper'
            restarted.ingest(rid,15,audio(15))
            await until(lambda:restarted.row(rid)['cursor']==30*16000)
            assert len(restarted.snapshot(rid)['segments'])==2
        finally:await restarted.close()
    asyncio.run(run())

def test_cancelled_whisper_terminates_process_releases_lock_and_never_claims_coverage(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        marker=tmp_path/'pid'
        class Slow(Whisper):
            def live_command(self,audio,output):
                return [sys.executable,'-c','import os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)',str(marker)]
        manager.batch_provider=Slow
        try:
            await manager.start(rid,WCONFIG);manager.ingest(rid,0,audio(15))
            await until(marker.exists)
            await manager.finish(rid,timeout=.05)
            assert not lock.locked() and manager.row(rid)['status']=='stopped'
            assert manager.cache.overview(rid,'whisper',15)['covered_seconds']==0
            assert manager.input_end(rid)==15*16000
            with pytest.raises(ProcessLookupError):os.kill(int(marker.read_text()),0)
        finally:await manager.close()
    asyncio.run(run())

def test_whisper_cache_replay_never_starts_process(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        kwargs=(Whisper(),manager.cache,lock,audio(15),0,rid,'browser',manager.store.root,{'boundary_reason':'standard'})
        first=[json.loads(line) async for line in whisper_events(*kwargs)]
        assert first[-1]['result']['persisted']
        class Cached(Whisper):
            def live_command(self,*args):raise AssertionError('must reuse cache')
        second=[json.loads(line) async for line in whisper_events(Cached(),*kwargs[1:])]
        assert second[-1]['result']['cached']
        assert second[-1]['result']['segments']==first[-1]['result']['segments']
    asyncio.run(run())
