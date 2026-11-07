"""D2 provenance diagnosis, concurrent-source guards and durable human feedback."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app import handoff_sources,minutes_versions,meeting_flow
from app.http_schemas import SourceReviewInput
from app.minutes import MeetingMinutes,MinutesItem
from app.notes import fingerprint

@pytest.fixture
def fixture(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成来源验收','fixture.wav'))
        store.save_revision(mid,[{'id':'s1','start':0,'end':2,'text':'周五提交材料','speaker':'甲'}],'manual')
        with store.connect() as db:
            aid=minutes_versions.publish_draft(db,mid,1,MeetingMinutes(title='合成',style='ai',overview=[MinutesItem(content='提交材料',source_segment_ids=['s1'])]),{})
        yield client,store,mid,aid

def read(f,**kwargs):
    _,s,m,_=f;return handoff_sources.report(s,m,**kwargs)
def body(f,item=None,**kwargs):
    item=item or read(f)['items'][1]
    return {'artifact_id':f[3],'source_id':item['source_id'],'fingerprint':item['fingerprint'],'decision':'reviewed','request_id':uid(),'confirmed':True,**kwargs}
def post(f,b):return f[0].post(f'/api/meetings/{f[2]}/handoff/source-reviews',json=b)
def change(f,text='周四提交材料',speaker='甲'):
    f[1].save_revision(f[2],[{'id':'s1','start':0,'end':2,'text':text,'speaker':speaker}],'manual')

def test_read_is_bounded_stable_no_writes(fixture):
    _,s,m,a=fixture
    with s.connect() as db:
        db.execute('BEGIN');first=handoff_sources.inspect(db,m,a);second=handoff_sources.inspect(db,m,a)
        assert db.total_changes==0 and first==second
    value=read(fixture,offset=1,limit=1)
    assert value['total']==2 and len(value['items'])==1 and value['items'][0]['target']=={'segment_id':'s1','start':0}
    assert value['items'][0]['before']=='周五提交材料'

def test_exact_historical_and_current_text_without_semantic_guess(fixture):
    change(fixture)
    value=read(fixture);item=value['items'][1]
    assert item['before']=='周五提交材料' and item['current']=='周四提交材料' and item['status']=='changed'
    assert value['reason_codes']==['transcript_changed']
    fixture[1].save_revision(fixture[2],[{'id':'s2','start':0,'end':2,'text':'周五提交材料','speaker':'甲'}],'manual')
    assert read(fixture)['items'][1]['status']=='missing' and read(fixture)['items'][1]['target'] is None

def test_missing_old_revision_is_explicit(fixture):
    fixture[1].execute('DELETE FROM revisions');change(fixture)
    assert read(fixture)['items'][1]['status']=='unavailable'

def test_acknowledgment_persists_but_never_repairs_or_adopts(fixture):
    c,s,m,a=fixture;change(fixture);before=c.get('/api/meetings/'+m).json()
    assert post(fixture,body(fixture)).status_code==200
    after=c.get('/api/meetings/'+m).json()
    assert before['handoff']==after['handoff'] and before['review']==after['review'] and before['active_minutes_id']==after['active_minutes_id']
    assert after['artifacts']==before['artifacts'] # internal receipts stay out of business artifacts
    assert read(fixture)['items'][1]['feedback']['decision']=='reviewed'
    with s.connect() as db:
        art=db.execute('SELECT * FROM artifacts WHERE id=?',(a,)).fetchone()
        assert meeting_flow.artifact_reasons(db,m,json.loads(art['provenance']),art['revision'])==['transcript_changed']
    receipt=s.one("SELECT payload FROM artifacts WHERE kind='handoff_review'")['payload']
    assert '周四' not in receipt and '周五' not in receipt

def test_repeated_command_and_request_id_collision(fixture):
    b=body(fixture);first=post(fixture,b);second=post(fixture,b)
    assert first.json()['id']==second.json()['id'] and second.json()['reused']
    assert post(fixture,{**b,'decision':'keep_history'}).status_code==409
    assert len(fixture[1].all("SELECT id FROM artifacts WHERE kind='handoff_review'"))==1

def test_source_change_rejects_new_stale_command_and_replay_does_not_reapply(fixture):
    b=body(fixture);assert post(fixture,b).status_code==200
    change(fixture)
    assert post(fixture,{**b,'request_id':uid()}).status_code==409
    assert post(fixture,b).json()['reused']
    assert read(fixture)['items'][1]['feedback']['stale']
    assert read(fixture)['counts']['processed']==0

def test_reset_and_reconfirm_remain_distinct_persistent_events(fixture):
    assert post(fixture,body(fixture)).status_code==200
    assert post(fixture,body(fixture,decision='reset')).status_code==200
    assert read(fixture)['counts']['processed']==0
    assert post(fixture,body(fixture,decision='keep_history')).status_code==200
    assert read(fixture)['counts']['processed']==1
    assert len(fixture[1].all("SELECT id FROM artifacts WHERE kind='handoff_review'"))==3

def test_cross_meeting_artifact_and_parent_never_disclose(fixture):
    c,s,m,a=fixture;other=uid();foreign=uid()
    s.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(other,'other','x'))
    s.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,1,'meeting_minutes',?,'{}')",(foreign,other,encode({'title':'PRIVATE'})))
    assert c.get(f'/api/meetings/{m}/handoff/sources?artifact_id={foreign}').status_code==404
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'source_minutes_dependencies':[foreign]}),a))
    value=read(fixture)
    assert 'PRIVATE' not in encode(value) and value['items'][-1]['status']=='missing' and value['items'][-1]['target'] is None
    assert post(fixture,body(fixture,artifact_id=foreign)).status_code==404

def test_parent_cycle_is_bounded_and_inspectable(fixture):
    _,s,_,a=fixture;s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'source_minutes_dependencies':[a]}),a))
    value=read(fixture);assert value['reason_codes']==['dependency_stale']
    assert value['items'][-1]['target']=={'artifact_id':a}

def test_note_change_and_deletion_locate_exact_note(fixture):
    c,s,m,a=fixture;nid=uid()
    s.execute("INSERT INTO meeting_notes(id,meeting_id,raw_text,note_scope,captured_at) VALUES(?,?,?,'meeting',1)",(nid,m,'核对材料'))
    note=s.one('SELECT * FROM meeting_notes WHERE id=?',(nid,))
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'adopted_notes':[{'note_id':nid,'note_fingerprint':fingerprint(note),'content':'历史采用内容'}]}),a))
    item=read(fixture)['items'][-1];assert item['status']=='current' and item['target']['note_id']==nid
    assert post(fixture,body(fixture,item)).status_code==200
    s.execute("UPDATE meeting_notes SET raw_text='修改后的简记' WHERE id=?",(nid,))
    item=read(fixture)['items'][-1];assert item['status']=='changed' and item['feedback']['stale'] and item['before']=='历史采用内容'
    s.execute('DELETE FROM meeting_notes WHERE id=?',(nid,))
    assert read(fixture)['items'][-1]['status']=='missing'

def test_context_only_hash_does_not_invent_field_history(fixture):
    _,s,m,a=fixture
    with s.connect() as db:old=meeting_flow.ai_context(db,m)[1]
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'flow_context_hash':old}),a))
    item=read(fixture)['items'][-1];assert item['status']=='current'
    with s.connect() as db:meeting_flow.save_brief(db,m,{'purpose':'新目标'})
    new=read(fixture)['items'][-1]
    assert new['status']=='changed' and new['fingerprint']!=item['fingerprint'] and '无法精确还原' in new['detail']

def test_full_content_and_speaker_changes_invalidate_fingerprint(fixture):
    change(fixture,'长'*900+'甲');first=read(fixture)['items'][1]
    change(fixture,'长'*900+'乙');second=read(fixture)['items'][1]
    assert first['current']==second['current'] and first['fingerprint']!=second['fingerprint']
    change(fixture,'长'*900+'乙','乙');assert read(fixture)['items'][1]['fingerprint']!=second['fingerprint']

def test_concurrent_duplicate_commands_write_once(fixture):
    _,s,m,_=fixture;b=SourceReviewInput(**body(fixture))
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:handoff_sources.review(s,m,b),range(2)))
    assert sorted(r['reused'] for r in results)==[False,True]

@pytest.mark.parametrize('suffix',['?offset=-1','?limit=51','?artifact_id='])
def test_report_input_bounds(fixture,suffix):
    assert fixture[0].get(f'/api/meetings/{fixture[2]}/handoff/sources'+suffix).status_code==422

def test_confirm_and_local_guards(fixture):
    assert post(fixture,body(fixture,confirmed=False)).status_code==409
    assert fixture[0].get(f'/api/meetings/{fixture[2]}/handoff/sources',headers={'host':'remote.example'}).status_code==403
    assert fixture[0].post(f'/api/meetings/{fixture[2]}/handoff/source-reviews',json=body(fixture),headers={'origin':'https://remote.example'}).status_code==403

def overview(fixture):
    _,s,m,_=fixture;rid=uid();item_id=uid()
    s.execute("INSERT INTO live_overview_runs(recording_id,config) VALUES(?,?)",(m,encode({'engine':'qwen'})))
    s.execute("INSERT INTO live_asr_ranges(id,recording_id,source,engine,start_frame,end_frame,audio_hash,identity,state,segments) VALUES(?,?,'browser','qwen',0,32000,'test','{}','completed',?)",(rid,m,encode([{'start':0,'end':2,'text':'周五提交材料'}])))
    payload={'id':item_id,'kind':'action','content':'周五提交','note_ids':[],'evidence':[{'range_id':rid,'segment_index':0,'segment_id':rid+':0','quote':'周五提交材料'}]}
    s.execute("INSERT INTO live_overview_reviews(recording_id,item_id,state,payload) VALUES(?,?,'confirmed',?)",(m,item_id,encode(payload)))
    return item_id,payload

def test_new_overview_snapshots_compare_exact_item_and_followup_change(fixture):
    _,s,m,a=fixture;item_id,payload=overview(fixture)
    with s.connect() as db:context,context_hash=meeting_flow.ai_context(db,m)
    assert context['reviewed_live_overview'][0]['item_id']==item_id
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'flow_context_hash':context_hash,'reviewed_live_overview':context['reviewed_live_overview']}),a))
    item=next(i for i in read(fixture)['items'] if i['kind']=='overview')
    assert item['status']=='current' and item['target']=={'item_id':item_id}
    assert post(fixture,body(fixture,item)).status_code==200
    s.execute("UPDATE live_overview_reviews SET state='ignored',version=version+1 WHERE item_id=?",(item_id,))
    item=next(i for i in read(fixture)['items'] if i['kind']=='overview')
    assert item['status']=='changed' and item['feedback']['stale']
    s.execute('DELETE FROM live_overview_reviews WHERE item_id=?',(item_id,))
    assert next(i for i in read(fixture)['items'] if i['kind']=='overview')['status']=='missing'

def test_old_overview_has_no_fabricated_history_and_empty_new_snapshot_has_no_false_sources(fixture):
    _,s,m,a=fixture;overview(fixture)
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'flow_context_hash':'old'}),a))
    item=next(i for i in read(fixture)['items'] if i['kind']=='overview')
    assert item['status']=='unavailable' and not item['before'] and '不能证明' in item['detail']
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'flow_context_hash':'old','reviewed_live_overview':[]}),a))
    assert not any(i['kind']=='overview' for i in read(fixture)['items'])

def test_parent_receipt_expires_when_dependency_changes_again(fixture):
    _,s,m,a=fixture;nid=uid();parent=uid()
    s.execute("INSERT INTO meeting_notes(id,meeting_id,raw_text,note_scope,captured_at) VALUES(?,?,?,'meeting',1)",(nid,m,'原简记'))
    old=fingerprint(s.one('SELECT * FROM meeting_notes WHERE id=?',(nid,)))
    s.execute("INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,1,'meeting_minutes','{}',?)",(parent,m,encode({'adopted_notes':[{'note_id':nid,'note_fingerprint':old}]})))
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode({'source_minutes_dependencies':[parent]}),a))
    s.execute("UPDATE meeting_notes SET raw_text='第一次修改' WHERE id=?",(nid,))
    item=next(i for i in read(fixture,artifact_id=a)['items'] if i['kind']=='minutes')
    assert post(fixture,body(fixture,item)).status_code==200
    s.execute("UPDATE meeting_notes SET raw_text='第二次修改' WHERE id=?",(nid,))
    item=next(i for i in read(fixture,artifact_id=a)['items'] if i['kind']=='minutes')
    assert item['feedback']['stale']


def test_review_returns_historical_translation_with_real_source_only(fixture):
    _,store,mid,aid=fixture
    store.execute('UPDATE artifacts SET provenance=? WHERE id=?',
                  (encode({'citation_translations':{'s1':'合成辅助译文','missing':'不关联'}}),aid))
    item=read(fixture)['items'][1]
    assert item['target']=={'segment_id':'s1','start':0}
    assert item['before']=='周五提交材料' and item['translation_zh']=='合成辅助译文'
    store.save_revision(mid,[],'manual')
    item=read(fixture)['items'][1]
    assert item['status']=='missing' and item['target'] is None
    assert item['translation_zh']=='合成辅助译文'
