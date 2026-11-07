"""D1 follow-up: interrupted work is inspectable; completed history stays optional."""
import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.meeting_review import MeetingReview
from app.minutes import MeetingMinutes,MinutesItem
from app import minutes_versions

@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid();store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成任务定位','test.wav'))
        store.save_revision(mid,[{'id':'s','start':0,'end':2,'speaker':'甲','text':'周五交付'}],'manual')
        yield client,store,mid

def job(store,mid,kind,state,config):
    jid=uid();store.execute('INSERT INTO jobs(id,meeting_id,kind,state,stage,config,error) VALUES(?,?,?,?,?,?,?)',(jid,mid,kind,state,'处理失败',encode(config),'private transcript or key'))
    return jid

def handoff(setup):
    c,_,mid=setup;return c.get('/api/meetings/'+mid).json()['handoff']

def stage(value,key):return next(row for row in value['stages'] if row['key']==key)

def publish(setup):
    _,s,m=setup
    with s.connect() as db:return minutes_versions.publish_draft(db,m,1,MeetingMinutes(title='合成纪要',style='ai',overview=[MinutesItem(content='交付',source_segment_ids=['s'])]),{})

def confirm(setup):
    _,s,m=setup;reviews=MeetingReview(s)
    for key in ('transcript','speakers','minutes'):
        state=reviews.read(m)['steps'][key]
        if state['state']!='blocked':reviews.set(m,key,state['fingerprint'],'confirmed')

@pytest.mark.parametrize('state',['failed','cancelled'])
def test_interrupted_transcription_has_exact_task_destination_without_auto_retry(setup,state):
    c,s,m=setup;s.execute("UPDATE meetings SET revision=0,segments='[]' WHERE id=?",(m,))
    jid=job(s,m,'transcription',state,{'base_revision':0})
    value=handoff(setup)
    assert value['primary']['target']=='tasks' and value['primary']['job_id']==jid
    assert stage(value,'transcript')['source']['interrupted_job_id']==jid
    assert 'private transcript' not in encode(value)
    assert s.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']==state

@pytest.mark.parametrize('kind',['minutes','analysis'])
def test_interrupted_minutes_or_analysis_followup_is_not_an_empty_generate_prompt(setup,kind):
    _,s,m=setup;confirm(setup)
    jid=job(s,m,kind,'failed',{'revision':1,**({'minutes_followup':{'settings':{}}} if kind=='analysis' else {})})
    value=handoff(setup)
    assert value['primary']['code']=='interrupted_minutes' and value['primary']['job_id']==jid
    assert '失败' in stage(value,'minutes')['summary']


def test_older_failure_does_not_hide_a_new_success_or_usable_adopted_version(setup):
    _,s,m=setup;job(s,m,'minutes','failed',{'revision':1});publish(setup);confirm(setup)
    assert handoff(setup)['primary']['code']=='delivery'


def test_current_failure_ignores_another_transcript_revision(setup):
    _,s,m=setup;confirm(setup);job(s,m,'minutes','failed',{'revision':99})
    assert handoff(setup)['primary']['code']=='generate_minutes'


def test_active_work_wins_over_a_previous_failure_and_carries_its_id(setup):
    _,s,m=setup;job(s,m,'minutes','failed',{'revision':1});jid=job(s,m,'minutes','queued',{'revision':1})
    assert handoff(setup)['primary']['job_id']==jid and handoff(setup)['primary']['code']=='processing'


def test_completed_stale_tasks_stay_visible_without_occupying_the_next_step(setup):
    c,s,m=setup;publish(setup);confirm(setup)
    s.execute("INSERT INTO note_todos(id,meeting_id,note_id,content,status,note_scope,source_kind,source_artifact_id,revision,note_fingerprint) VALUES(?,?,'','已完成历史事项','done','meeting','minutes',?,1,'')",(uid(),m,uid()))
    value=handoff(setup);actions=stage(value,'actions')
    assert actions['counts']['historical_stale']==1 and actions['counts']['active_stale']==0
    assert actions['counts']['stale']==1 and value['primary']['code']=='delivery'
    s.execute("UPDATE note_todos SET status='pending' WHERE meeting_id=?",(m,))
    value=handoff(setup)
    assert stage(value,'actions')['counts']['active_stale']==1 and value['primary']['code']=='review_actions'


def test_task_scope_is_exact_and_can_find_old_rows_outside_global_first_page(setup):
    c,s,m=setup;jid=job(s,m,'minutes','failed',{'revision':1})
    s.execute("UPDATE jobs SET created='2000-01-01 00:00:00' WHERE id=?",(jid,))
    other=uid();s.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(other,'其他会议','other.wav'))
    for _ in range(55):job(s,other,'analysis','failed',{'revision':1})
    assert jid not in [item['id'] for item in c.get('/api/runtime/tasks?state=all').json()['items']]
    result=c.get('/api/runtime/tasks',params={'state':'all','meeting_id':m,'job_id':jid}).json()
    assert [item['id'] for item in result['items']]==[jid]
    assert result['counts']=={'failed':1} and not result['live_overview']
    assert 'private transcript' not in encode(result)
    assert c.get('/api/runtime/tasks',params={'state':'all','meeting_id':other,'job_id':jid}).json()['items']==[]


def test_scoped_task_remains_discoverable_after_cancel_retry_and_completion(setup):
    c,s,m=setup;jid=job(s,m,'minutes','cancelled',{'revision':1})
    params={'state':'all','meeting_id':m,'job_id':jid}
    assert c.post('/api/jobs/'+jid+'/retry',headers={'X-Task-Attempt':'0'}).status_code==200
    assert c.get('/api/runtime/tasks',params=params).json()['items'][0]['state']=='queued'
    s.execute("UPDATE jobs SET state='completed' WHERE id=?",(jid,))
    assert c.get('/api/runtime/tasks',params=params).json()['items'][0]['state']=='completed'

@pytest.mark.parametrize('scope',[{'meeting_id':''},{'job_id':'x'*101},{'meeting_id':"' OR 1=1--",'job_id':'none'}])
def test_scope_parameters_are_bounded_and_parameterized(setup,scope):
    c,s,m=setup;job(s,m,'minutes','failed',{'revision':1})
    response=c.get('/api/runtime/tasks',params={'state':'all',**scope})
    assert response.status_code==400 if not scope.get('meeting_id') or len(scope.get('job_id',''))>100 else response.json()['items']==[]


@pytest.mark.parametrize('change',['revision','context'])
def test_irrecoverably_outdated_minutes_disable_retry_and_offer_new_generation(setup,change):
    c,s,m=setup
    from app.meeting_flow import ai_context,save_brief
    with s.connect() as db:_,context_hash=ai_context(db,m)
    jid=job(s,m,'minutes','failed',{'revision':1,'operation':'compose','title':'合成任务定位','context_hash':context_hash})
    if change=='revision':s.save_revision(m,[{'id':'s','start':0,'end':2,'speaker':'甲','text':'更新原文'}],'manual')
    else:
        with s.connect() as db:save_brief(db,m,{'purpose':'新目的'})
    result=c.get('/api/runtime/tasks',params={'state':'all','meeting_id':m,'job_id':jid}).json()['items'][0]
    assert not result['can_retry'] and '重新生成' in result['retry_blocked']
    assert c.post('/api/jobs/'+jid+'/retry').status_code==409
    assert not s.all("SELECT * FROM job_runtime_events WHERE job_id=? AND action='retry'",(jid,))


def test_confirmed_action_follows_series_with_one_identity_and_original_source(setup):
    from app.meeting_flow import publish_preparation

    client, store, origin = setup
    minutes = MeetingMinutes(title='系列纪要', action_items=[
        MinutesItem(content='交付原型', source_segment_ids=['s']),
        MinutesItem(content='补充材料', source_segment_ids=['s']),
    ])
    with store.connect() as db:
        artifact = minutes_versions.publish_draft(db, origin, 1, minutes, {'edited': True})
    path = f'/api/meetings/{origin}/minutes/{artifact}/actions'
    def track(index, content):
        result = client.post(f'{path}/{index}/track', json={'content':content,'confirmed':True})
        assert result.status_code == 201, result.text
        return result.json()

    pending = track(0, '交付原型')['id']
    assert track(0, '交付原型') == {'id':pending,'reused':True}
    done = track(1, '补充材料')['id']
    assert client.put(f'/api/meetings/{origin}/note-todos/{done}', json={'status':'done'}).status_code == 200

    def next_meeting(previous):
        mid = uid()
        with store.connect() as db:
            db.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'系列下一场','test.wav'))
            publish_preparation(db,mid,{'previous_meeting_id':previous})
        value = client.get(f'/api/meetings/{mid}').json()
        candidates = value['flow']['previous']['todos']
        assert [task['id'] for task in candidates] == [pending]
        assert candidates[0]['meeting_id'] == origin
        assert candidates[0]['source_artifact_id'] == artifact
        return mid

    second = next_meeting(origin)
    for _ in range(2):
        assert client.post(f'/api/meetings/{second}/tasks/{pending}/carry').status_code == 201
    third = next_meeting(second)
    assert client.post(f'/api/meetings/{third}/tasks/{pending}/carry').status_code == 201
    assert client.put(f'/api/meetings/{third}/note-todos/{pending}', json={'status':'done'}).status_code == 200
    assert {row['id']:row['status'] for row in client.get('/api/followups').json()} == {pending:'done',done:'done'}
