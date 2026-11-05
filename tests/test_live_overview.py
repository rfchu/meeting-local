import json,time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.recordings import RecordingStore
from app.live_overview import EnableInput,ReviewInput,OverviewOutput,digest,approved_context
from app.notes import insert_note
from app.meeting_flow import ai_context

@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    app.state.live_overview.start_monitor=lambda:None
    with TestClient(app) as client:
        store=app.state.store;recordings=RecordingStore(store);session=recordings.create('audio/webm');rid=session['id']
        session.update(duration=45,updated=time.time(),notes=[{'client_id':'private','captured_at':10.0,'note_type':'question','note_scope':'personal','raw_text':'私人提醒，不要发给模型','overview_focus':False},
            {'client_id':'focus','captured_at':20.0,'note_type':'question','note_scope':'personal','raw_text':'关注交付时间','overview_focus':True}])
        recordings.write(recordings.folder(rid)/'session.json',session)
        pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,?,?,?)',(pid,'本地概览','local','http://localhost:1234/v1','qwen-test'))
        service=app.state.live_overview
        def add(start,end,text,engine='qwen'):
            aid=uid();store.execute("INSERT INTO live_asr_ranges(id,recording_id,source,engine,start_frame,end_frame,audio_hash,identity,state,segments) VALUES(?,?,'browser',?,?,?,'test','{}','completed',?)",(aid,rid,engine,round(start*16000),round(end*16000),encode([{'start':start,'end':end,'text':text}])))
            return aid+':0'
        sid=add(0,30,'首版支持录音和会议纪要，陈明负责周五前交付预览稿。上线日期还没有确定。')
        yield client,store,service,recordings,rid,pid,sid,add

def enable(setup):
    client,store,service,recordings,rid,pid,*_=setup
    result=client.put(f'/api/recordings/{rid}/overview',json={'profile_id':pid,'engine':'qwen'})
    assert result.status_code==200,result.text
    return store.one('SELECT * FROM live_overview_runs WHERE recording_id=?',(rid,))

def plan(setup):
    _,_,service,_,rid,*_=setup
    return service.prepare(rid,enable(setup),True)

def output(sid,content='陈明负责周五前交付预览稿。',kind='action',item_id=None,note_ids=None):
    return {'items':[{'id':item_id,'kind':kind,'content':content,'evidence':[{'segment_id':sid,'quote':'陈明负责周五前交付预览稿'}],'note_ids':note_ids or []}]}

def first(setup,note_ids=None):
    _,_,service,_,rid,_,sid,_=setup
    p=plan(setup);service.publish(p,output(sid,note_ids=note_ids));return service.read(rid)['items'][0]

def review(service,rid,item,state='confirmed',content=None):
    return service.decide(rid,item['id'],ReviewInput(state=state,fingerprint=item['fingerprint'],version=item['review_version'],content=content))

def test_opt_in_does_not_call_model_and_filters_private_notes(setup):
    client,store,service,_,rid,*_=setup
    assert client.get(f'/api/recordings/{rid}/overview').json()['state']=='unconfigured'
    enable(setup);p=plan(setup)
    assert '私人提醒' not in encode(p['input'])
    assert [n['id'] for n in p['input']['focus_notes']]==['focus']
    assert not store.one('SELECT id FROM jobs') and not store.one('SELECT recording_id FROM live_overview_snapshots')

def test_evidence_unknown_items_and_unselected_notes_are_rejected_without_losing_previous(setup):
    _,_,service,_,rid,_,sid,_=setup
    item=first(setup);version=service.latest(rid)['version']
    for change in [{'id':'invented'},{'evidence':[{'segment_id':'not-confirmed','quote':'陈明负责'}]},
                   {'evidence':[{'segment_id':sid,'quote':'不存在的引用'}]},{'note_ids':['private']}]:
        value=output(sid);value['items'][0].update(change)
        with pytest.raises(ValueError):service.publish(plan(setup),value)
        assert service.latest(rid)['version']==version
    with pytest.raises(ValueError):service.publish(plan(setup),{'items':[]})
    assert service.read(rid)['items'][0]['id']==item['id']

def test_incremental_windows_stable_ids_and_no_call_without_new_content(setup):
    _,_,service,recordings,rid,_,sid,add=setup
    item=first(setup)
    run=enable(setup)
    assert service.prepare(rid,run,False) is None
    sid2=add(30,45,'测试清单由王宁负责，下一次评审再确认上线日期。')
    p=service.prepare(rid,run,False)
    assert len(p['input']['new_segments'])==1 and p['input']['new_segments'][0]['id']==sid2
    assert sid in [s['id'] for s in p['input']['context']]
    service.publish(p,output(sid,item_id=item['id']))
    assert service.read(rid)['items'][0]['id']==item['id']
    assert service.latest(rid)['cursor']==2

def test_user_confirmation_and_edit_are_preserved_during_inflight_model_update(setup):
    _,_,service,_,rid,_,sid,_=setup
    item=first(setup);p=plan(setup)
    result=review(service,rid,item,content='陈明确认周五前提供预览稿，待核验。')
    service.publish(p,output(sid,content='模型试图覆盖已确认内容',item_id=item['id']))
    saved=service.read(rid)['items'][0]
    assert saved['content']=='陈明确认周五前提供预览稿，待核验。' and saved['decision']=='confirmed'
    assert saved['id']==item['id']
    with pytest.raises(ValueError,match='已变化'):review(service,rid,item)

def test_confirmed_quote_changes_and_focus_withdrawal_mark_items_stale(setup):
    _,store,service,recordings,rid,_,sid,_=setup
    item=first(setup,['focus']);review(service,rid,item)
    session=recordings.read(rid);session['notes'][1]['overview_focus']=False
    recordings.write(recordings.folder(rid)/'session.json',session)
    assert service.read(rid)['items'][0]['stale']
    with pytest.raises(ValueError,match='依据已变化'):review(service,rid,service.read(rid)['items'][0])
    p=plan(setup);assert not p['input']['current_items'] and not p['input']['focus_notes']
    session['notes'][1]['overview_focus']=True;recordings.write(recordings.folder(rid)/'session.json',session)
    aid=sid.split(':')[0]
    store.execute('UPDATE live_asr_ranges SET segments=? WHERE id=?',(encode([{'start':0,'end':30,'text':'已校对为其他内容'}]),aid))
    assert service.read(rid)['items'][0]['stale']

def test_disabled_finished_or_changed_inputs_cannot_publish_late_results(setup):
    _,store,service,recordings,rid,_,sid,_=setup
    p=plan(setup);service.disable(rid)
    from app.live_overview import Deferred
    with pytest.raises(Deferred):service.publish(p,output(sid))
    p=plan(setup);session=recordings.read(rid);session['notes'][1]['raw_text']='关注点变化'
    recordings.write(recordings.folder(rid)/'session.json',session)
    with pytest.raises(Deferred):service.publish(p,output(sid))
    p=plan(setup);session['state']='completed';recordings.write(recordings.folder(rid)/'session.json',session)
    with pytest.raises(Deferred):service.publish(p,output(sid))
    assert not service.latest(rid)

def test_formal_jobs_and_asr_backlog_defer_without_acquiring_asr_lock(setup):
    _,store,service,recordings,rid,_,sid,_=setup
    enable(setup);called=[];service.generator=lambda p:called.append(True) or output(sid)
    store.execute("INSERT INTO jobs(id,meeting_id,state,kind) VALUES('formal',?,'queued','analysis')",(rid,))
    service.run_once(rid,True);assert not called
    store.execute("DELETE FROM jobs WHERE id='formal'")
    session=recordings.read(rid);session['duration']=100
    recordings.write(recordings.folder(rid)/'session.json',session)
    service.run_once(rid,True);assert not called
    session['duration']=45;recordings.write(recordings.folder(rid)/'session.json',session)
    service.run_once(rid,True);assert called==[True]
    assert service.read(rid)['version']==1

def test_invalid_model_response_preserves_last_snapshot_and_stops_after_three_failures(setup):
    _,store,service,_,rid,*_=setup;first(setup)
    service.generator=lambda _:(_ for _ in ()).throw(ValueError('模型失败'))
    for n in range(3):service.run_once(rid,True)
    status=service.read(rid);assert status['version']==1 and status['state']=='failed' and not status['enabled']
    assert len(status['items'])==1

def test_cloud_consent_binds_endpoint_and_changed_configuration_blocks_dispatch(setup):
    client,store,service,_,rid,_,sid,_=setup
    pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,?,?,?,?)',(pid,'云端','cloud','https://example.com/v1','qwen','never-in-config'))
    url=f'/api/recordings/{rid}/overview'
    assert client.put(url,json={'profile_id':pid,'confirm_cloud':True}).status_code==409
    expected={'id':pid,'provider_type':'cloud','base_url':'https://example.com/v1','model':'qwen'}
    assert client.put(url,json={'profile_id':pid,'confirm_cloud':True,'expected_provider':expected}).status_code==200
    assert 'never-in-config' not in store.one('SELECT config FROM live_overview_runs WHERE recording_id=?',(rid,))['config']
    store.execute('UPDATE llm_configs SET base_url=? WHERE id=?',('https://other.example.com/v1',pid))
    service.generator=lambda _:pytest.fail('配置变化后不能发送')
    service.run_once(rid,True)
    assert '配置已变化' in service.read(rid)['error']

def test_after_meeting_priorities_require_unique_quote_mapping_and_track_note_changes(setup):
    _,store,service,recordings,rid,_,sid,_=setup
    item=first(setup,['focus']);review(service,rid,item)
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(rid,'会中概览测试','test.wav'))
    raw=json.loads(store.one('SELECT segments FROM live_asr_ranges WHERE id=?',(sid.split(':')[0],))['segments'])
    segments=[dict(raw[0],id='formal-1',speaker='陈明')]
    store.save_revision(rid,segments,'manual',expected=0)
    with store.connect() as db:
        for note in recordings.read(rid)['notes']:insert_note(db,rid,note)
        context,key=ai_context(db,rid)
    assert context['reviewed_live_overview'][0]['source_segment_ids']==['formal-1']
    store.execute("UPDATE meeting_notes SET raw_text='人工修改关注点' WHERE meeting_id=? AND client_id='focus'",(rid,))
    with store.connect() as db:
        assert not approved_context(db,rid,segments)
        assert ai_context(db,rid)[1]!=key
    assert service.read(rid)['items'][0]['stale']

def test_unmatched_or_ambiguous_formal_quotes_are_not_carried_to_minutes(setup):
    _,store,service,_,rid,*_=setup;item=first(setup);review(service,rid,item)
    with store.connect() as db:
        assert not approved_context(db,rid,[{'id':'new','start':0,'end':30,'text':'完全不同的文字'}])
        source={'start':0,'end':30,'text':'陈明负责周五前交付预览稿'}
        assert not approved_context(db,rid,[dict(source,id='a'),dict(source,id='b')])

def test_snapshot_history_is_bounded_and_old_versions_do_not_replace_current(setup):
    _,_,service,_,rid,_,sid,_=setup
    item=first(setup)
    for n in range(14):service.publish(plan(setup),output(sid,content=f'第{n}版草稿',item_id=item['id']))
    current=service.read(rid)
    assert len(current['history'])==12 and current['version']==15
    previous=service.read(rid,14);assert previous['historical'] and previous['version']==14
    assert service.read(rid)['version']==15

def test_recording_metadata_retains_focus_and_client_identity(setup):
    client,_,_,recordings,rid,*_=setup
    body={'duration':45,'chunk_count':0,'notes':[{'client_id':'stable-note','captured_at':10,'raw_text':'关注期限','note_scope':'personal','overview_focus':True}]}
    result=client.put(f'/api/recordings/{rid}/metadata',json=body)
    assert result.status_code==200,result.text
    note=recordings.read(rid)['notes'][0]
    assert note['client_id']=='stable-note' and note['overview_focus'] and note['note_scope']=='personal'


def test_superseded_raw_range_is_not_reused_in_formal_minutes(setup):
    _,store,service,_,rid,_,sid,add=setup
    item=first(setup);review(service,rid,item)
    add(0,30,'同一音频范围的新识别结果，不再使用旧词句。')
    with store.connect() as db:
        assert not approved_context(db,rid,[{'id':'formal','start':0,'end':30,'text':'陈明负责周五前交付预览稿'}])
    assert service.read(rid)['items'][0]['stale']

def test_restoring_ignored_item_stays_visible_without_forcing_model_call(setup):
    _,_,service,_,rid,_,sid,_=setup
    item=first(setup);review(service,rid,item,'dismissed')
    service.publish(plan(setup),{'items':[]})
    ignored=service.read(rid)['items'][0]
    assert ignored['decision']=='dismissed'
    review(service,rid,ignored,'pending')
    assert service.read(rid)['items'][0]['decision']=='pending'

def test_schema_parser_supports_overview_and_keeps_retry_budget():
    from scripts.lmstudio_analyze import complete_json
    calls=[]
    def request(url,headers,payload):
        calls.append(payload)
        return {'choices':[{'message':{'content':'{"items":[' if len(calls)==1 else '{"items":[]}'}}]}
    payload={'model':'test','max_tokens':3072,'messages':[],
        'response_format':{'type':'json_schema','json_schema':{'name':'meeting_overview'}}}
    assert complete_json('http://localhost',{},payload,requester=request)=={'items':[]}
    assert [c['max_tokens'] for c in calls]==[3072,3072]


def test_snapshot_records_model_provenance_and_never_needs_the_asr_lock(setup):
    import threading
    client,store,service,_,rid,pid,sid,_=setup
    enable(setup);service.generator=lambda _:output(sid)
    lock=client.app.state.worker.asr_lock;lock.acquire()
    thread=threading.Thread(target=lambda:service.run_once(rid,True),daemon=True)
    try:
        thread.start();thread.join(timeout=2)
        assert not thread.is_alive(), '概览不能等待 ASR 排他锁'
    finally:
        lock.release();thread.join(timeout=2)
    snapshot=service.read(rid)['snapshot_provider']
    assert snapshot['id']==pid and snapshot['model']=='qwen-test' and snapshot['provider_type']=='local'


def test_changes_to_focus_context_beyond_incremental_cursor_block_publication(setup):
    _,store,service,recordings,rid,_,sid,add=setup
    last=None
    for n in range(22):last=add(30+n*15,45+n*15,'上下文内容'*100)
    session=recordings.read(rid);session['notes'][1]['captured_at']=350.0
    recordings.write(recordings.folder(rid)/'session.json',session)
    p=plan(setup)
    assert p['cursor']<len(p['parts'])
    assert any(s['id']==last for s in p['input']['context'])
    store.execute('UPDATE live_asr_ranges SET segments=? WHERE id=?',(encode([{'start':345,'end':360,'text':'已修订的远端上下文'}]),last.split(':')[0]))
    from app.live_overview import Deferred
    with pytest.raises(Deferred):service.publish(p,output(sid))
    assert service.latest(rid) is None

def test_generation_deadline_allows_large_local_model_and_worker_errors_are_safe():
    from app.live_overview import generation_timeout,worker_failure
    assert generation_timeout({'provider_type':'local'})==180
    assert generation_timeout({'provider_type':'cloud'})==120
    assert '认证' in worker_failure('HTTP Error 401: secret-response-body')
    assert '无法连接' in worker_failure('urllib.error.URLError: sensitive-endpoint')
    assert 'JSON' in worker_failure('RuntimeError: 模型连续返回不完整或不符合结构的 JSON：private text')
    assert 'secret' not in worker_failure('Traceback: secret-response-body')

def test_failed_first_version_can_retry_and_pause_retains_diagnostic(setup):
    client,store,service,_,rid,*_=setup
    enable(setup)
    service.generator=lambda _:(_ for _ in ()).throw(ValueError('概览生成超过 45 秒'))
    service.run_once(rid,force=True)
    result=service.read(rid)
    assert result['version']==0 and result['failures']==1
    assert result['confirmed_count']==1 and result['confirmed_until']==30
    assert result['timeout_seconds']==180 and result['retry_in_seconds']>0
    assert client.post(f'/api/recordings/{rid}/overview/refresh').status_code==200
    paused=service.disable(rid)
    assert not paused['enabled'] and '45 秒' in paused['error']
    enable(setup)
    assert service.read(rid)['failures']==0
    store.execute('UPDATE live_overview_runs SET last_attempt=0 WHERE recording_id=?',(rid,))
    assert client.post(f'/api/recordings/{rid}/overview/refresh').status_code==200
    assert store.one('SELECT force FROM live_overview_runs WHERE recording_id=?',(rid,))['force']==1

def test_local_generation_survives_sixty_seconds_instead_of_old_45s_cutoff(setup,monkeypatch):
    import app.live_overview as module
    from pathlib import Path
    _,_,service,_,_,*_=setup
    request=plan(setup)
    class Process:
        returncode=0
        def __init__(self,command,**kwargs):
            self.polls=0
            Path(command[command.index('--output')+1]).write_text('{"items": []}')
        def poll(self):
            self.polls+=1
            return None if self.polls==1 else 0
    times=iter([0,60])
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:next(times)))
    monkeypatch.setattr(module.subprocess,'Popen',Process)
    monkeypatch.setattr(service.closed,'wait',lambda _:False)
    assert service.generate(request).items==[]

def test_numbered_sources_are_verbatim_and_model_never_needs_to_copy_quotes(setup):
    from app.live_overview import numbered_input,expand_numbered
    _,_,service,_,_,_,sid,*_=setup
    first(setup,['focus']);p=plan(setup)
    data,quotes=numbered_input(p['input'])
    assert 'context' not in data and 'new_segments' not in data
    assert data['current_items'][0]['id']=='i1' and data['focus_notes'][0]['id']=='n1'
    assert data['current_items'][0]['note_ids']==['n1']
    assert all(q['quote'] in next(s['text'] for s in p['parts'] if s['id']==q['segment_id']) for q in quotes.values())
    eid=data['current_items'][0]['evidence_ids'][0]
    raw={'items':[{'id':'i1','kind':'action','content':'陈明负责周五前交付预览稿。','evidence_ids':[eid],'note_ids':['n1']}]}
    result=expand_numbered(raw,quotes,{'i1':p['input']['current_items'][0]['id']},{'n1':'focus'})
    assert result.items[0].evidence[0].segment_id==sid
    assert result.items[0].evidence[0].quote=='陈明负责周五前交付预览稿'
    assert result.items[0].note_ids==['focus']
    unknown=json.loads(json.dumps(raw));unknown['items'][0]['id']='i999';unknown['items'][0]['note_ids']=['n999']
    normalized=expand_numbered(unknown,quotes,{'i1':'old'},{'n1':'focus'})
    assert normalized.items[0].id is None and normalized.items[0].note_ids==[]
    bad=json.loads(json.dumps(raw));bad['items'][0]['evidence_ids']=['e999']
    with pytest.raises(ValueError,match='缺少有效证据'):expand_numbered(bad,quotes)
    mixed={'items':raw['items']+bad['items']}
    assert len(expand_numbered(mixed,quotes).items)==1
    assert expand_numbered(mixed,quotes).filtered_count==1


def test_incremental_update_retains_omitted_grounded_points_and_replaces_current_topic(setup):
    _,_,service,_,rid,_,sid,add=setup
    old=first(setup)
    sid2=add(30,45,'王宁负责下周一交付测试清单。')
    p=plan(setup)
    service.publish(p,{'items':[{'id':None,'kind':'point','content':'测试清单由王宁负责。','evidence':[{'segment_id':sid2,'quote':'王宁负责下周一交付测试清单'}],'note_ids':[]}]})
    items=service.read(rid)['items']
    assert len(items)==2 and any(i['id']==old['id'] for i in items)
    p=plan(setup)
    service.publish(p,output(sid,kind='topic',content='确定交付分工'))
    p=plan(setup)
    service.publish(p,{'items':[{'id':None,'kind':'topic','content':'讨论测试清单','evidence':[{'segment_id':sid2,'quote':'王宁负责下周一交付测试清单'}],'note_ids':[]}]})
    assert [i['content'] for i in service.read(rid)['items'] if i['kind']=='topic']==['讨论测试清单']

def test_queued_index_never_blocks_live_overview_but_running_index_does(setup):
    _,store,service,_,rid,*_=setup
    run=enable(setup)
    store.execute("INSERT INTO jobs(id,state,kind,config) VALUES('background-index','queued','retrieval_index','{}')")
    assert service.prepare(rid,run,False) is not None
    store.execute("UPDATE jobs SET state='running' WHERE id='background-index'")
    from app.live_overview import Deferred
    with pytest.raises(Deferred):service.prepare(rid,run,False)

def test_repeated_punctuation_variants_reuse_ids_and_do_not_grow_snapshot(setup):
    _,_,service,_,rid,_,sid,_=setup
    old=first(setup)
    result=output(sid,content=old['content'].rstrip('。'))
    result['items']*=2
    service.publish(plan(setup),result)
    saved=service.read(rid)
    assert len(saved['items'])==1 and saved['items'][0]['id']==old['id']
    assert saved['quality']['duplicates_removed']==1

def test_decision_replaces_resolved_question_with_same_identity(setup):
    _,_,service,_,rid,_,sid,add=setup
    service.publish(plan(setup),output(sid,kind='question',content='上线日期还没有确定。'))
    old=service.read(rid)['items'][0]
    sid2=add(30,45,'大家明确同意下周三上线。')
    service.publish(plan(setup),{'items':[{'id':old['id'],'kind':'decision','content':'大家明确同意下周三上线。','evidence':[{'segment_id':sid2,'quote':'大家明确同意下周三上线'}],'note_ids':[]}]})
    saved=service.read(rid)['items']
    assert len(saved)==1 and saved[0]['id']==old['id'] and saved[0]['kind']=='decision'
