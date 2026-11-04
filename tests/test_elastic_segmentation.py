import asyncio,json
import pytest
from fastapi.testclient import TestClient
from app.live_segmentation import elastic_plan,RATE
from app.live_results import plan_ranges
from app.transcript_paragraphs import paragraphs
from app.main import create_app
from test_live_session import setup,until,audio,CONFIG

VOICE=b'\x70\x17'
def raw(seconds):return VOICE*round(seconds*RATE)

def test_15_seconds_is_soft_while_25_seconds_is_hard():
    assert elastic_plan(raw(15),15)['confirmed'] is False
    assert elastic_plan(raw(15),15)['extending'] is True
    assert elastic_plan(raw(24),24)['confirmed'] is False
    final=elastic_plan(raw(25),25)
    assert final['confirmed'] and final['boundary_reason']=='hard_limit' and final['count']==25*RATE
    assert elastic_plan(raw(26)+b'\0\0'*RATE,25)['boundary_reason']=='hard_limit'

def test_acoustic_pause_can_commit_before_next_preview_but_punctuation_is_not_an_input():
    plan=elastic_plan(raw(18)+b'\0\0'*round(.8*RATE),20)
    assert plan['confirmed'] and plan['boundary_reason']=='silence'
    assert plan['count']==round(18.8*RATE)
    assert elastic_plan(raw(18)+b'\0\0'*round(.6*RATE),20) is None
    assert elastic_plan(raw(.7),3,'stop')['count']==round(.7*RATE)
    assert elastic_plan(raw(.3),3,'stop') is None

def test_hard_cut_links_next_audio_without_gaps_and_survives_reconnect(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            manager.ingest(rid,0,audio(25))
            await until(lambda:manager.row(rid)['cursor']==25*RATE)
            from app.live_session import pcm_wav
            manager.ingest(rid,25,pcm_wav(raw(4)+b'\0\0'*round(.8*RATE)))
            await until(lambda:manager.row(rid)['cursor']==round(29.8*RATE))
            snapshot=manager.snapshot(rid);segments=snapshot['segments']
            assert len(segments)==2 and segments[0]['continues_next']
            assert segments[1]['continues_previous'] and not segments[1]['continues_next']
            assert segments[0]['continuation_id']==segments[1]['continuation_id']
            assert len(paragraphs(segments))==1
            overview=manager.cache.overview(rid,'qwen',29.8)
            assert not overview['gaps'] and overview['covered_seconds']==29.8
            replay=manager.events(rid,0)
            assert json.loads(await anext(replay))['segments']==segments
            await replay.aclose()
        finally:await manager.close()
    asyncio.run(run())

def test_pause_or_leading_silence_breaks_continuation_even_after_a_hard_cut(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            manager.ingest(rid,0,audio(25))
            await until(lambda:manager.row(rid)['cursor']==25*RATE)
            window={'offset':25,'next_offset':28,'boundary_reason':'silence','leading_silence_seconds':0,'skipped_seconds':0}
            assert manager.continuation(rid,window,-1)['continues_previous']
            assert not manager.continuation(rid,window,25*RATE)['continues_previous']
            assert not manager.continuation(rid,window|{'leading_silence_seconds':.8},-1)['continues_previous']
            assert not manager.continuation(rid,window|{'skipped_seconds':3},-1)['continues_previous']
        finally:await manager.close()
    asyncio.run(run())

def test_lag_is_bounded_but_skipped_audio_never_claims_successful_coverage(tmp_path):
    async def run():
        manager,recordings,rid,resident,lock=setup(tmp_path)
        try:
            await manager.start(rid,CONFIG)
            manager.ingest(rid,0,audio(40));manager.ingest(rid,40,audio(30))
            await until(lambda:manager.row(rid)['cursor']==70*RATE)
            overview=manager.cache.overview(rid,'qwen',70)
            assert overview['gaps']==[{'start':0,'end':45}]
            assert overview['covered_seconds']==25
            assert len(list((recordings.folder(rid)/'live-input').glob('*.wav')))==2
        finally:await manager.close()
    asyncio.run(run())

def segments():
    common={'speaker':'未分配','boundary_review':False,'continuation_id':'test:0'}
    return [{**common,'id':'a','start':0,'end':25,'text':'这个功能计划在下周','continues_previous':False,'continues_next':True,'boundary_reason':'hard_limit'},
            {**common,'id':'b','start':25,'end':30,'text':'完成验证，然后发布。','continues_previous':True,'continues_next':False,'boundary_reason':'silence'}]

def test_text_exports_group_reading_paragraphs_while_json_subtitles_and_edits_keep_ranges(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        store=client.app.state.store;mid='elastic-export'
        store.execute("INSERT INTO meetings(id,title,filename,duration) VALUES(?,'合成连续发言','test.wav',30)",(mid,))
        values=segments();store.save_revision(mid,values,'synthetic')
        for fmt in ('txt','md'):
            text=client.get(f'/api/meetings/{mid}/export/{fmt}').text
            assert '这个功能计划在下周完成验证，然后发布。' in text
            assert text.count('未分配')==1
        srt=client.get(f'/api/meetings/{mid}/export/srt').text
        assert srt.count('-->')==2 and '00:00:25,000' in srt
        exported=client.get(f'/api/meetings/{mid}/export/json').json()['segments']
        assert exported==values
        response=client.put(f'/api/meetings/{mid}/transcript',json={'revision':1,'segments':values})
        assert response.status_code==200,response.text
        assert client.get(f'/api/meetings/{mid}/export/txt').text.count('未分配')==1
        altered=[dict(s) for s in values];altered[1]['speaker']='另一位'
        assert len(paragraphs(altered))==2
        altered[1].update(speaker='未分配',start=26)
        assert len(paragraphs(altered))==2
