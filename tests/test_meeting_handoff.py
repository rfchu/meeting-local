"""D1 read-only lineage and next-step projection; no model/device calls."""
import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.meeting_handoff import snapshot
from app.meeting_review import MeetingReview
from app.minutes import MeetingMinutes,MinutesItem
from app import meeting_flow,minutes_versions

@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid();store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,10)',(mid,'合成交接','fixture.wav'))
        store.save_revision(mid,[{'id':'s1','start':0,'end':3,'text':'周五由甲交付材料。','speaker':'甲'}],'manual')
        yield client,store,mid

def read(setup):
    c,s,m=setup
    return c.get('/api/meetings/'+m).json()['handoff']

def stage(value,key):return next(item for item in value['stages'] if item['key']==key)

def publish(setup,provenance=None,actions=False):
    _,store,mid=setup
    doc=MeetingMinutes(title='合成纪要',style='ai',overview=[MinutesItem(content='合成交付',source_segment_ids=['s1'])],action_items=[MinutesItem(content='交付材料',source_segment_ids=['s1'])] if actions else [])
    with store.connect() as db:
        return minutes_versions.publish_draft(db,mid,store.one('SELECT revision FROM meetings WHERE id=?',(mid,))['revision'],doc,provenance or {})

def confirm(setup,steps=('transcript','speakers','minutes')):
    _,store,mid=setup;review=MeetingReview(store)
    for key in steps:
        value=review.read(mid)['steps'][key]
        if value['state'] not in ('blocked','not_needed'):review.set(mid,key,value['fingerprint'],'confirmed')


def note(setup,scope='meeting'):
    c,_,m=setup
    return c.post(f'/api/meetings/{m}/notes',json={'raw_text':'需核对交付','captured_at':1,'note_scope':scope}).json()['id']

def decide(setup,nid,decision):
    c,_,m=setup;value=c.get(f'/api/meetings/{m}/notes/{nid}/review').json()
    r=c.put(f'/api/meetings/{m}/notes/{nid}/disposition',json={'decision':decision,'confirmed':True,'fingerprint':value['fingerprint'],'revision':value['revision'],'content':'周五交付' if decision=='meeting' else '', 'source_segment_ids':['s1'] if decision=='meeting' else []})
    assert r.status_code==200,r.text


def test_snapshot_is_read_only_stable_and_does_not_expose_note_content(setup):
    _,store,mid=setup;note(setup,'personal')
    with store.connect() as db:
        db.execute('BEGIN');first=snapshot(db,mid);second=snapshot(db,mid)
        assert db.total_changes==0 and first==second
    assert '需核对交付' not in encode(first) and first['version']==1
    assert len(first['stages'])==6 and first['primary']['code']=='review_transcript'


def test_missing_transcript_is_not_replaced_by_live_confirmation_cursor(setup):
    _,s,m=setup;s.execute("UPDATE meetings SET revision=0,segments='[]' WHERE id=?",(m,))
    s.execute("INSERT INTO live_sessions(recording_id,source,status,config,cursor) VALUES(?,'browser','stopped','{}',160000)",(m,))
    value=read(setup)
    assert stage(value,'transcript')['state']=='missing' and value['primary']['code']=='transcribe'


def test_processing_cta_remains_an_observable_destination(setup):
    _,s,m=setup;jid=uid()
    s.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,?,'minutes','queued','等待资源','{}')",(jid,m))
    value=read(setup);assert value['primary']['target']=='minutes' and value['primary']['code']=='processing'
    assert stage(value,'minutes')['state']=='processing'


@pytest.mark.parametrize('decision',['personal','deferred'])
def test_private_and_deferred_notes_do_not_block_delivery(setup,decision):
    nid=note(setup,'personal');decide(setup,nid,decision);publish(setup);confirm(setup)
    value=read(setup)
    assert stage(value,'notes')['state']=='optional'
    assert value['primary']['code']=='delivery'
    assert not any(edge['from']=='notes' for edge in value['edges'])


def test_meeting_scope_notes_require_review_then_are_only_generation_context(setup):
    _,s,m=setup;nid=note(setup);confirm(setup,('transcript',))
    assert read(setup)['primary']['code']=='review_notes'
    decide(setup,nid,'meeting')
    with s.connect() as db:context,context_hash=meeting_flow.ai_context(db,m)
    publish(setup,{'flow_context_hash':context_hash})
    value=read(setup)
    assert stage(value,'notes')['counts']['confirmed_meeting']==1
    assert stage(value,'notes')['counts']['adopted']==0
    assert {'from':'notes','to':'minutes','relation':'context','count':1} in value['edges']
    assert not any(edge['relation']=='explicit_adoption' for edge in value['edges'])


def test_changed_approved_note_invalidates_context_and_next_required_review(setup):
    c,s,m=setup;nid=note(setup);decide(setup,nid,'meeting')
    with s.connect() as db:_,context_hash=meeting_flow.ai_context(db,m)
    publish(setup,{'flow_context_hash':context_hash});confirm(setup)
    before=read(setup)
    assert c.put(f'/api/meetings/{m}/notes/{nid}',json={'raw_text':'已修改','note_type':'note','note_scope':'meeting'}).status_code==200
    after=read(setup)
    assert stage(after,'minutes')['state']=='stale'
    assert 'meeting_context_changed' in stage(after,'minutes')['source']['reason_codes']
    assert after['primary']['code']=='review_notes' and after['source_version']!=before['source_version']


def test_stale_parent_is_rejected_consistently_across_consumers(setup):
    c,s,m=setup;parent=publish(setup)
    s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'改为周一交付'}],'manual')
    child=publish(setup,{'source_minutes_dependencies':[parent]})
    with s.connect() as db:minutes_versions.adopt(db,m,child)
    detail=c.get('/api/meetings/'+m).json();value=detail['handoff']
    assert 'dependency_stale' in stage(value,'minutes')['source']['reason_codes']
    assert next(a for a in detail['artifacts'] if a['id']==child)['stale']
    assert detail['review']['steps']['minutes']['state']=='blocked'
    assert not detail['flow']['has_current_minutes']
    assert c.post(f'/api/meetings/{m}/minutes/{child}/adopt').status_code==409


@pytest.mark.parametrize('change',['revision','context','missing_parent','cycle'])
def test_shared_reason_codes_keep_existing_outputs_and_report_change(setup,change):
    _,s,m=setup
    with s.connect() as db:_,context_hash=meeting_flow.ai_context(db,m)
    aid=publish(setup,{'flow_context_hash':context_hash})
    if change=='revision':s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'新原文'}],'manual')
    if change=='context':
        with s.connect() as db:meeting_flow.save_brief(db,m,{'purpose':'新目的'})
    if change in ('missing_parent','cycle'):
        s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'source_minutes_dependencies':[uid() if change=='missing_parent' else aid]}),aid))
    value=read(setup);state=stage(value,'minutes')
    assert state['state']=='stale' and state['source']['reason_codes']
    assert s.one('SELECT id FROM artifacts WHERE id=?',(aid,))


def test_new_draft_does_not_replace_stale_adopted_version(setup):
    _,s,m=setup;first=publish(setup)
    s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'新版原文'}],'manual')
    latest=publish(setup);confirm(setup,('transcript','speakers'))
    value=read(setup)
    assert stage(value,'minutes')['source']['artifact_id']==first
    assert stage(value,'minutes')['source']['latest_current_draft']==latest
    assert value['primary']['code']=='choose_draft'


def test_actions_are_candidates_until_explicit_tracking_and_completion_not_required(setup):
    c,s,m=setup;aid=publish(setup,actions=True);confirm(setup)
    value=read(setup);assert value['primary']['code']=='review_actions'
    assert stage(value,'actions')['counts']['candidates']==1
    response=c.post(f'/api/meetings/{m}/minutes/{aid}/actions/0/track',json={'content':'交付材料','owner':'甲','due_date':'周五','confirmed':True})
    assert response.status_code in (200,201),response.text
    value=read(setup)
    assert stage(value,'actions')['counts']['pending']==1 and stage(value,'actions')['counts']['candidates']==0
    assert value['primary']['code']=='delivery'
    assert any(edge['relation']=='confirmed_tracking' for edge in value['edges'])


def test_live_reuse_is_explicit_and_manual_revision_is_not_misattributed(setup):
    _,s,m=setup
    s.execute('UPDATE revisions SET source=? WHERE meeting_id=? AND revision=1',(encode({'engine':'qwen','kind':'live_reuse','reused_ranges':2}),m))
    value=read(setup);assert value['edges'][0]['relation']=='reused_audio_ranges'
    s.save_revision(m,[{'id':'s1','start':0,'end':3,'text':'校对稿','speaker':'甲'}],'manual')
    value=read(setup);assert stage(value,'transcript')['source']['origin']=='revision'
    assert not any(edge['from']=='live_asr' for edge in value['edges'])


def test_overview_cursor_is_a_segment_count_and_unconfirmed_items_are_optional(setup):
    _,s,m=setup
    s.execute("INSERT INTO live_overview_snapshots(recording_id,version,cursor,covered_hash,meta_hash,payload) VALUES(?,1,4,'h','m',?)",(m,encode({'items':[]})))
    publish(setup);confirm(setup)
    value=read(setup)
    assert stage(value,'overview')['state']=='review'
    assert stage(value,'overview')['source']['covered_segments']==4
    assert 'covered_seconds' not in stage(value,'overview')['source']
    assert value['primary']['code']=='delivery'


def test_pending_recording_observation_takes_priority_without_changing_source(setup):
    _,s,m=setup;confirm(setup,('transcript',))
    with s.connect() as db:
        db.execute('BEGIN');value=snapshot(db,m,{'pending_count':2});assert db.total_changes==0
    assert value['primary']['target']=='recording'


def test_read_transaction_does_not_mix_a_new_transcript_with_old_inputs(setup):
    _,s,m=setup;publish(setup)
    with s.connect() as db:
        db.execute('BEGIN');assert db.execute('SELECT revision FROM meetings WHERE id=?',(m,)).fetchone()[0]==1
        s.save_revision(m,[{'id':'s1','start':0,'end':3,'text':'并发新稿','speaker':'甲'}],'manual')
        old=snapshot(db,m)
        assert old['revision']==1 and stage(old,'minutes')['state']=='review'
    fresh=read(setup);assert fresh['revision']==2 and stage(fresh,'minutes')['state']=='stale'


def test_adopting_a_new_draft_does_not_make_the_old_original_a_pending_draft(setup):
    _,s,m=setup;publish(setup);chosen=publish(setup)
    with s.connect() as db:minutes_versions.adopt(db,m,chosen)
    confirm(setup)
    value=read(setup)
    assert stage(value,'minutes')['counts']['current_drafts']==0
    assert value['primary']['code']=='delivery'


def test_confirmed_adopted_version_can_be_delivered_with_other_optional_drafts(setup):
    _,_,_=setup;publish(setup);publish(setup);confirm(setup)
    value=read(setup);assert stage(value,'minutes')['counts']['current_drafts']==1
    assert value['primary']['code']=='delivery'


def test_delivery_snapshot_is_preserved_and_never_claimed_as_already_sent(setup):
    _,s,m=setup;aid=publish(setup);confirm(setup)
    from app.minutes_delivery import create
    receipt=create(s,m,aid,False,True)
    value=read(setup)
    assert value['primary']['code']=='preview' and stage(value,'delivery')['state']=='ready'
    assert '不等于已发送' in stage(value,'delivery')['summary']
    s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'修改原文'}],'manual')
    assert stage(read(setup),'delivery')['state']=='missing'
    assert s.one('SELECT id FROM minutes_deliveries WHERE id=?',(receipt['id'],))


def test_explicit_note_adoption_is_distinct_from_context(setup):
    _,s,m=setup;nid=note(setup);decide(setup,nid,'meeting')
    from app.notes import fingerprint
    source=s.one('SELECT * FROM meeting_notes WHERE id=?',(nid,))
    publish(setup,{'adopted_notes':[{'note_id':nid,'note_fingerprint':fingerprint(source)}]})
    value=read(setup)
    assert stage(value,'notes')['counts']['adopted']==1
    assert any(edge['relation']=='explicit_adoption' for edge in value['edges'])
    assert not any(edge['relation']=='context' for edge in value['edges'])


def test_overview_confirmation_requires_current_quote_mapping_and_is_only_context(setup):
    _,s,m=setup;rid=uid();oid=uid()
    s.execute("INSERT INTO live_asr_ranges(id,recording_id,source,engine,start_frame,end_frame,audio_hash,identity,state,segments) VALUES(?,?,'browser','qwen',0,48000,'h','{}','completed',?)",(rid,m,encode([{'start':0,'end':3,'text':'周五由甲交付材料。'}])))
    s.execute("INSERT INTO live_overview_runs(recording_id,config,enabled) VALUES(?, ?,0)",(m,encode({'engine':'qwen'})))
    s.execute("INSERT INTO live_overview_snapshots(recording_id,version,cursor,covered_hash,meta_hash,payload) VALUES(?,1,1,'h','m',?)",(m,encode({'items':[]})))
    item={'id':oid,'kind':'action','content':'候选交付','evidence':[{'range_id':rid,'segment_index':0,'quote':'周五由甲交付'}]}
    s.execute("INSERT INTO live_overview_reviews(recording_id,item_id,state,payload) VALUES(?,?,'confirmed',?)",(m,oid,encode(item)))
    with s.connect() as db:_,context_hash=meeting_flow.ai_context(db,m)
    publish(setup,{'flow_context_hash':context_hash})
    value=read(setup);assert stage(value,'overview')['counts']['confirmed_usable']==1
    assert {'from':'overview','to':'minutes','relation':'context','count':1} in value['edges']
    s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'改为周一交付'}],'manual')
    value=read(setup)
    assert stage(value,'overview')['state']=='stale' and stage(value,'overview')['counts']['stale_confirmed']==1
    assert not any(edge['from']=='overview' for edge in value['edges'])


def test_tracked_actions_keep_their_history_when_source_changes(setup):
    c,s,m=setup;aid=publish(setup,actions=True)
    response=c.post(f'/api/meetings/{m}/minutes/{aid}/actions/0/track',json={'content':'交付材料','owner':'甲','due_date':'周五','confirmed':True})
    assert response.status_code in (200,201)
    task_id=response.json()['id']
    s.save_revision(m,[{'id':'s1','start':0,'end':3,'speaker':'甲','text':'修改后的安排'}],'manual')
    value=read(setup)
    assert stage(value,'actions')['state']=='stale'
    assert stage(value,'actions')['counts']['stale']==1
    assert s.one('SELECT id FROM note_todos WHERE id=?',(task_id,))


@pytest.mark.parametrize('kind',['analysis','transcription'])
def test_planned_minutes_are_visible_during_their_preceding_step(setup,kind):
    _,s,m=setup
    s.execute("INSERT INTO jobs(id,meeting_id,kind,state,stage,config) VALUES(?,?,?,'queued','等待前序步骤',?)",(uid(),m,kind,encode({'minutes_followup':{'settings':{'method':'ai'}}})))
    value=read(setup)
    assert stage(value,'minutes')['state']=='processing'
    assert '前序步骤' in stage(value,'minutes')['summary']
    assert value['primary']['target']=='tasks'
