"""D3 transactional review-to-delivery handoff; no inference or capture."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app import minutes_versions,delivery_readiness,minutes_delivery
from app.minutes import MeetingMinutes,MinutesItem
from app.http_schemas import DeliveryFinalizeInput
from app.meeting_review import MeetingReview

@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as c:
        s=app.state.store;m=uid()
        s.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(m,'合成交付检查','synthetic.wav'))
        s.save_revision(m,[{'id':'s1','start':0,'end':2,'speaker':'甲','text':'周五提交材料。'}],'manual')
        doc=MeetingMinutes(title='合成纪要',style='ai',overview=[MinutesItem(content='周五交付',source_segment_ids=['s1'])],action_items=[MinutesItem(content='提交材料',source_segment_ids=['s1'])])
        with s.connect() as db:a=minutes_versions.publish_draft(db,m,1,doc,{})
        yield c,s,m,a,doc

def read(f,citations=False):return f[0].get(f'/api/meetings/{f[2]}/minutes/delivery-readiness?include_citations={str(citations).lower()}').json()
def confirm(f,steps=('transcript','speakers')):
    review=MeetingReview(f[1])
    for step in steps:
        item=review.read(f[2])['steps'][step];review.set(f[2],step,item['fingerprint'],'skipped' if step=='speakers' else 'confirmed')
def body(f,**patch):return {'artifact_id':f[3],'fingerprint':read(f)['fingerprint'],'include_citations':False,'confirmed':True,**patch}
def post(f,b=None):return f[0].post(f'/api/meetings/{f[2]}/minutes/delivery-finalize',json=b or body(f))
def handoff(f):return f[0].get('/api/meetings/'+f[2]).json()['handoff']
def track(f):
    r=f[0].post(f'/api/meetings/{f[2]}/minutes/{f[3]}/actions/0/track',json={'content':'提交材料','owner':'甲','due_date':'周五','confirmed':True});assert r.status_code==201;return r.json()['id']

def test_read_is_stable_readonly_and_does_not_expose_transcript_or_secrets(setup):
    _,s,m,_,_=setup
    with s.connect() as db:
        db.execute('BEGIN');a=delivery_readiness.snapshot(db,m);b=delivery_readiness.snapshot(db,m)
        assert a==b and db.total_changes==0
    assert '周五提交材料' not in encode(a) and a['version']==1
    assert {i['code'] for i in a['blockers']}=={'review_transcript','review_speakers'}
    assert not a['can_finalize'] and a['minutes_review_state']=='pending'

def test_earlier_checks_required_but_minutes_is_confirmed_atomically_at_finish(setup):
    assert post(setup).status_code==409
    confirm(setup);value=read(setup);assert value['can_finalize']
    response=post(setup);assert response.status_code==201,response.text
    review=MeetingReview(setup[1]).read(setup[2]);assert review['steps']['minutes']['state']=='done'
    value=read(setup);assert value['current_delivery_id']==response.json()['id']
    assert handoff(setup)['primary']['code']=='preview' # untracked candidates are advisory after explicit delivery
    saved=json.loads(setup[1].one('SELECT payload FROM minutes_deliveries')['payload'])
    assert saved['review_confirmation']['minutes']==review['steps']['minutes']['fingerprint']
    assert not setup[1].all('SELECT * FROM note_todos') # no implicit commitments

def test_only_meeting_intended_unresolved_notes_block(setup):
    c,s,m,_,_=setup;confirm(setup)
    nid=c.post(f'/api/meetings/{m}/notes',json={'raw_text':'个人想法','captured_at':1,'note_scope':'personal'}).json()['id']
    assert read(setup)['can_finalize']
    c.post(f'/api/meetings/{m}/notes',json={'raw_text':'待核对会议事项','captured_at':1,'note_scope':'meeting'})
    assert not read(setup)['can_finalize'] and any(i['code']=='review_notes' for i in read(setup)['blockers'])
    assert post(setup).status_code==409

@pytest.mark.parametrize('mutation',['revision','title','brief','adopted','review_reset','task','citations'])
def test_stale_confirmation_token_rejected_without_partial_review_or_delivery(setup,mutation):
    c,s,m,a,doc=setup;confirm(setup)
    tid=track(setup) if mutation=='task' else None
    b=body(setup)
    if mutation=='revision':s.save_revision(m,[{'id':'s1','start':0,'end':2,'speaker':'甲','text':'周四交付'}],'manual')
    elif mutation=='title':s.execute("UPDATE meetings SET title='新主题' WHERE id=?",(m,))
    elif mutation=='brief':c.put(f'/api/meetings/{m}/brief',json={'purpose':'新目标'})
    elif mutation=='adopted':
        with s.connect() as db:
            new=minutes_versions.publish_draft(db,m,1,doc,{});minutes_versions.adopt(db,m,new)
    elif mutation=='review_reset':MeetingReview(s).reset(m,'speakers')
    elif mutation=='task':c.put(f'/api/meetings/{m}/note-todos/{tid}',json={'owner':'乙','status':'done'})
    elif mutation=='citations':b['include_citations']=True
    result=post(setup,b);assert result.status_code==409,result.text
    assert not s.all('SELECT * FROM minutes_deliveries')
    assert not s.one("SELECT * FROM meeting_reviews WHERE step='minutes'")

def test_confirmation_rollback_when_snapshot_write_fails(setup,monkeypatch):
    _,s,m,_,_=setup;confirm(setup)
    b=DeliveryFinalizeInput(**body(setup))
    def fail(*args):raise OSError('synthetic disk failure')
    monkeypatch.setattr(minutes_delivery,'persist',fail)
    with pytest.raises(OSError):delivery_readiness.finalize(s,m,b)
    assert not s.one("SELECT * FROM meeting_reviews WHERE step='minutes'")
    assert not s.all('SELECT * FROM minutes_deliveries')

def test_concurrent_and_response_lost_retries_return_one_receipt(setup):
    _,s,m,_,_=setup;confirm(setup);b=DeliveryFinalizeInput(**body(setup))
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:delivery_readiness.finalize(s,m,b),range(2)))
    assert len({r['id'] for r in results})==1 and sorted(r['reused'] for r in results)==[False,True]
    # After sources change or manual reset, replay never reconfirms the current content.
    MeetingReview(s).reset(m,'minutes');s.save_revision(m,[{'id':'s2','start':0,'end':2,'text':'变更','speaker':'乙'}],'manual')
    assert delivery_readiness.finalize(s,m,b)['reused']
    assert not s.one("SELECT * FROM meeting_reviews WHERE step='minutes'")
    assert len(s.all('SELECT * FROM minutes_deliveries'))==1 and read(setup)['current_delivery_id'] is None

def test_fresh_finish_of_already_current_snapshot_is_idempotent(setup):
    confirm(setup);first=post(setup).json();second=post(setup).json()
    assert second['id']==first['id'] and second['reused']
    assert len(setup[1].all('SELECT * FROM minutes_deliveries'))==1

def test_task_snapshot_drift_and_reconfirmation_preserve_old_exports(setup):
    c,s,m,a,_=setup;confirm(setup);tid=track(setup)
    first=post(setup).json();path=f"/api/meetings/{m}/minutes/export/md?delivery_id={first['id']}";original=c.get(path).content
    c.put(f'/api/meetings/{m}/note-todos/{tid}',json={'status':'done','owner':'乙','due_date':'下周'})
    assert read(setup)['current_delivery_id'] is None
    delivery_stage=next(i for i in handoff(setup)['stages'] if i['key']=='delivery');assert delivery_stage['state']=='review'
    second=post(setup).json();assert second['id']!=first['id'] and second['task_status'][0]['owner']=='乙'
    assert c.get(path).content==original
    assert read(setup)['current_delivery_id']==second['id']

def test_source_processing_receipt_never_satisfies_checkpoints_or_repairs_stale_minutes(setup):
    c,s,m,a,_=setup;confirm(setup)
    s.save_revision(m,[{'id':'s1','start':0,'end':2,'speaker':'甲','text':'周四交付'}],'manual')
    item=c.get(f'/api/meetings/{m}/handoff/sources').json()['items'][0]
    assert c.post(f'/api/meetings/{m}/handoff/source-reviews',json={'artifact_id':a,'source_id':item['source_id'],'fingerprint':item['fingerprint'],'request_id':uid(),'decision':'reviewed','confirmed':True}).status_code==200
    value=read(setup);assert not value['can_finalize'] and any(i['code']=='invalid_minutes' for i in value['blockers'])
    assert post(setup).status_code==409

def test_unrelated_draft_and_personal_note_do_not_require_adoption_or_notes_checkpoint(setup):
    c,s,m,_,doc=setup;confirm(setup)
    with s.connect() as db:minutes_versions.publish_draft(db,m,1,doc,{})
    c.post(f'/api/meetings/{m}/notes',json={'raw_text':'个人记录','captured_at':0,'note_scope':'personal'})
    assert read(setup)['can_finalize'] and post(setup).status_code==201

def test_stale_active_tasks_warn_but_do_not_require_task_completion(setup):
    _,s,m,a,_=setup;confirm(setup);tid=track(setup)
    # Same meeting can also contain an old unrelated task; it remains visible, never rewritten.
    s.execute("UPDATE note_todos SET source_artifact_id='missing' WHERE id=?",(tid,))
    value=read(setup);assert value['can_finalize'] and any(i['code']=='stale_tasks' for i in value['warnings'])
    assert post(setup).status_code==201
    assert s.one('SELECT status FROM note_todos WHERE id=?',(tid,))['status']=='pending'

def test_input_local_guards_and_foreign_artifact(setup):
    c,s,m,a,_=setup;confirm(setup)
    assert post(setup,{**body(setup),'confirmed':False}).status_code==409
    assert post(setup,{**body(setup),'artifact_id':uid()}).status_code==409
    assert post(setup,{**body(setup),'fingerprint':'invalid'}).status_code==422
    assert c.get(f'/api/meetings/{m}/minutes/delivery-readiness',headers={'host':'remote.example'}).status_code==403
    assert c.post(f'/api/meetings/{m}/minutes/delivery-finalize',json=body(setup),headers={'origin':'https://remote.example'}).status_code==403

def test_task_source_validity_change_requires_a_new_snapshot_even_when_execution_unchanged(setup):
    _,s,m,a,_=setup;confirm(setup);tid=track(setup)
    first=post(setup).json()
    s.execute("UPDATE note_todos SET source_artifact_id='missing' WHERE id=?",(tid,))
    value=read(setup);assert value['current_delivery_id'] is None and value['can_finalize']
    second=post(setup).json();assert second['id']!=first['id']
    assert second['task_status']==first['task_status'] # source validity is separately bound


def audience_preview(f, **patch):
    return f[0].post(f'/api/meetings/{f[2]}/minutes/audience-preview',json={
        'artifact_id':f[3],'audience':'external','selection':[],**patch})


def test_external_preview_is_empty_by_default_and_filters_internal_material(setup):
    c,s,m,a,doc=setup
    doc.title='PRIVATE-TITLE'
    doc.purpose='PRIVATE-PURPOSE'
    doc.interviewer_evaluation='PRIVATE-ASSESSMENT'
    doc.overview += [
        MinutesItem(content='PRIVATE-NOTE',source_segment_ids=['s1']),
        MinutesItem(content='PRIVATE-ASSESSMENT',source_segment_ids=['s1']),
        MinutesItem(content='PRIVATE-UNSOURCED'),
    ]
    doc.overview[0].owner='PRIVATE-OWNER'
    doc.conclusions=[MinutesItem(content='对外讨论要点',source_segment_ids=['s1'])]
    c.post(f'/api/meetings/{m}/notes',json={'raw_text':'PRIVATE-NOTE','note_scope':'personal','captured_at':1})
    s.execute('UPDATE artifacts SET payload=? WHERE id=?',(encode(doc.model_dump()),a))
    response=audience_preview(setup,include_citations=True)
    assert response.status_code==200,response.text
    value=response.json()
    assert '周五交付' not in value['markdown']
    assert 'PRIVATE-' not in response.text
    assert [item['id'] for item in value['candidates']]==['overview:0','conclusions:0']
    for blocked in ('action_items:0','overview:1','overview:2','overview:3','summary:0'):
        assert audience_preview(setup,selection=[blocked]).status_code==409
    selected=audience_preview(setup,selection=['overview:0','conclusions:0'],include_citations=True).json()
    assert all(word in selected['markdown'] for word in ('对外会议纪要','周五交付','对外讨论要点'))
    assert all(word not in selected['markdown'] for word in ('PRIVATE-','00:00','周五提交材料'))
    assert 'PRIVATE-TITLE' in audience_preview(setup,audience='internal').json()['markdown']
    assert not s.all('SELECT * FROM minutes_deliveries')


def confirm_audience(f, fingerprint, **patch):
    return f[0].post(f'/api/meetings/{f[2]}/minutes/audience-finalize',json={
        'artifact_id':f[3], 'audience':'external', 'selection':['overview:0'],
        'fingerprint':fingerprint, 'confirmed':True, **patch})


def test_audiences_confirm_independently_and_replay_is_idempotent(setup):
    confirm(setup)
    internal = audience_preview(setup,audience='internal').json()['fingerprint']
    external = audience_preview(setup,selection=['overview:0']).json()['fingerprint']
    assert confirm_audience(setup,external,audience='internal',selection=[]).status_code==409
    assert confirm_audience(setup,external,selection=[]).status_code==409
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:confirm_audience(setup,external),range(2)))
    assert all(r.status_code==201 for r in responses)
    assert len({r.json()['id'] for r in responses})==1
    assert sorted(r.json()['reused'] for r in responses)==[False,True]
    assert handoff(setup)['primary']['code']=='preview'
    assert confirm_audience(setup,internal,audience='internal',selection=[]).status_code==409
    internal = audience_preview(setup,audience='internal').json()['fingerprint']
    saved = confirm_audience(setup,internal,audience='internal',selection=[])
    assert saved.status_code==201,saved.text
    assert saved.json()['id']!=responses[0].json()['id']
    fresh=audience_preview(setup,selection=['overview:0']).json()['fingerprint']
    assert confirm_audience(setup,fresh).json()['id']==responses[0].json()['id']
    assert len(setup[1].all('SELECT id FROM minutes_deliveries'))==2
    history=setup[0].get(f'/api/meetings/{setup[2]}').json()['deliveries']
    assert {row['audience'] for row in history}=={'internal','external'}


def test_late_audience_confirmation_rejects_changed_adoption(setup):
    _,s,m,_,doc=setup
    confirm(setup)
    preview=audience_preview(setup,selection=['overview:0']).json()
    with s.connect() as db:
        new=minutes_versions.publish_draft(db,m,1,doc,{})
        minutes_versions.adopt(db,m,new)
    assert confirm_audience(setup,preview['fingerprint']).status_code==409
    assert not s.all('SELECT * FROM minutes_deliveries')


def test_external_action_requires_confirmed_matching_wording(setup):
    tid=track(setup)
    selection=['action_items:0']
    assert audience_preview(setup,selection=selection).status_code==200
    setup[1].execute("UPDATE note_todos SET content=? WHERE id=?",('另有已核对措辞',tid))
    assert audience_preview(setup,selection=selection).status_code==409
