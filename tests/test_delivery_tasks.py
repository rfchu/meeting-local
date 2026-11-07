import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.minutes import MeetingMinutes,MinutesItem
from app import minutes_versions

@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as c:
        store=app.state.store
        def meeting(title):
            mid=uid();store.directory(mid).mkdir();store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,title,'sample.wav'))
            store.save_revision(mid,[{'id':'s1','start':0,'end':12,'speaker':'甲','text':'周五交付。'}],'manual')
            return mid
        mid=meeting('会议一');other=meeting('会议二')
        doc=MeetingMinutes(title='交付纪要',style='ai',action_items=[MinutesItem(content='交付原型',source_segment_ids=['s1'],owner='甲',due_date='周五')])
        with store.connect() as db:aid=minutes_versions.publish_draft(db,mid,1,doc,{'edited':True})
        yield c,store,mid,other,aid,doc


def track(c,mid,aid):
    response=c.post(f'/api/meetings/{mid}/minutes/{aid}/actions/0/track',json={'content':'交付原型','owner':'甲','due_date':'周五','confirmed':True})
    assert response.status_code==201
    return response.json()['id']


def deliver(c,mid,aid,**patch):
    return c.post(f'/api/meetings/{mid}/minutes/deliveries',json={'artifact_id':aid,'confirmed':True,'include_citations':True,**patch})


def edit(c,mid,aid,doc):
    result=c.put(f'/api/meetings/{mid}/minutes',json={'source_minutes_id':aid,'document':doc.model_dump()})
    assert result.status_code==201,result.text
    return result.json()['id']


def test_task_identity_survives_edit_reorder_but_not_changed_action_or_unrelated_ai(setup):
    c,store,mid,other,aid,doc=setup;tid=track(c,mid,aid)
    doc.title='新标题';doc.action_items.insert(0,MinutesItem(content='其他事项',source_segment_ids=['s1']))
    newer=edit(c,mid,aid,doc)
    assert c.get(f'/api/meetings/{mid}').json()['action_tasks'][newer]=={'1':tid}
    doc.action_items[1].content='变更交付范围';changed=edit(c,mid,newer,doc)
    assert c.get(f'/api/meetings/{mid}').json()['action_tasks'][changed]=={}
    with store.connect() as db:
        independent=minutes_versions.publish_draft(db,mid,1,MeetingMinutes(title='重新生成',action_items=[MinutesItem(content='交付原型',source_segment_ids=['s1'],owner='甲',due_date='周五')]),{})
    assert c.get(f'/api/meetings/{mid}').json()['action_tasks'][independent]=={}
    assert len(c.get('/api/followups').json())==1


def test_carry_is_same_entity_and_completion_shared_without_duplicate_global_tasks(setup):
    c,store,mid,other,aid,doc=setup;tid=track(c,mid,aid)
    path=f'/api/meetings/{other}/tasks/{tid}/carry'
    assert c.post(path).status_code==409
    c.put(f'/api/meetings/{other}/brief',json={'previous_meeting_id':mid})
    assert c.post(path).status_code==201 and c.post(path).status_code==201
    task=c.get(f'/api/meetings/{other}').json()['note_todos'][0]
    assert task['id']==tid and task['carried'] and task['source_meeting_title']=='会议一'
    c.put(f'/api/meetings/{other}/note-todos/{tid}',json={'status':'done','owner':'乙','due_date':'下周'})
    assert c.get(f'/api/meetings/{mid}').json()['note_todos'][0]['status']=='done'
    assert len(c.get('/api/followups').json())==1
    assert c.delete(f'/api/meetings/{mid}').status_code==409
    assert c.delete(f'/api/meetings/{other}/tasks/{tid}/link').status_code==200
    assert c.put(f'/api/meetings/{other}/note-todos/{tid}',json={'status':'pending'}).status_code==404
    assert c.get(f'/api/meetings/{other}').json()['note_todos']==[]
    assert c.delete(f'/api/meetings/{mid}').status_code==200


def test_carry_private_done_stale_unrelated_tasks_are_rejected(setup):
    c,store,mid,other,aid,doc=setup;tid=track(c,mid,aid)
    c.put(f'/api/meetings/{other}/brief',json={'previous_meeting_id':mid})
    for column,value in [('note_scope','personal'),('status','done'),('revision',0)]:
        if column=='revision':
            store.execute('UPDATE artifacts SET revision=0 WHERE id=?',(aid,))
        else:store.execute(f'UPDATE note_todos SET {column}=? WHERE id=?',(value,tid))
        assert c.post(f'/api/meetings/{other}/tasks/{tid}/carry').status_code==409
        store.execute("UPDATE note_todos SET note_scope='meeting',status='pending' WHERE id=?",(tid,))
    assert c.get(f'/api/meetings/{other}').json()['note_todos']==[]


def test_delivery_frozen_after_draft_task_transcript_changes_and_restart(setup,tmp_path):
    c,store,mid,other,aid,doc=setup;tid=track(c,mid,aid)
    response=deliver(c,mid,aid);assert response.status_code==201,response.text
    snapshot=response.json();did=snapshot['id']
    assert snapshot['sent_status']=='unknown'
    assert snapshot['task_status'][0]['status']=='pending'
    assert deliver(c,mid,aid).json()['id']==did
    path=f'/api/meetings/{mid}/minutes/export/md?delivery_id={did}'
    original=c.get(path).content
    assert '交付原型' in original.decode()
    c.put(f'/api/meetings/{mid}/note-todos/{tid}',json={'status':'done','owner':'乙','due_date':'周一'})
    next_delivery=deliver(c,mid,aid).json();assert next_delivery['id']!=did and next_delivery['task_status'][0]['status']=='done'
    doc.title='后来修改的标题';doc.action_items[0].content='后来修改的行动';edit(c,mid,aid,doc)
    store.save_revision(mid,[{'id':'s2','start':0,'end':1,'speaker':'甲','text':'原文变了'}],'manual',expected=1)
    assert c.get(path).content==original
    assert c.get(path+'&include_citations=false').content==original
    assert c.get(f'/api/meetings/{other}/minutes/export/md?delivery_id={did}').status_code==404
    c.__exit__(None,None,None)  # Release the previous service before a real restart.
    with TestClient(create_app(tmp_path,start_worker=False)) as restarted:
        assert restarted.get(path).content==original
        snapshots=restarted.get(f'/api/meetings/{mid}').json()['deliveries']
        assert len(snapshots)==2 and snapshots[-1]['task_status'][0]['owner']=='甲'
        assert restarted.get(f'/api/meetings/{mid}/minutes/export/docx?delivery_id={did}').content[:2]==b'PK'


def test_delivery_requires_confirmation_adopted_current_nonempty_content(setup):
    c,store,mid,other,aid,doc=setup
    assert deliver(c,mid,aid,confirmed=False).status_code==409
    assert deliver(c,other,aid).status_code==409
    with store.connect() as db:draft=minutes_versions.publish_draft(db,mid,1,doc,{})
    assert deliver(c,mid,draft).status_code==409
    store.save_revision(mid,[{'id':'s2','start':0,'end':1,'speaker':'甲','text':'修改'}],'manual',expected=1)
    assert deliver(c,mid,aid).status_code==409
    assert not store.all('SELECT * FROM minutes_deliveries')


def test_task_bound_confirmation_does_not_overwrite_existing_execution(setup):
    c,store,mid,other,aid,doc=setup;tid=track(c,mid,aid)
    c.put(f'/api/meetings/{mid}/note-todos/{tid}',json={'status':'done','owner':'乙','due_date':'下周'})
    doc.title='标题修订';newer=edit(c,mid,aid,doc)
    result=c.post(f'/api/meetings/{mid}/minutes/{newer}/actions/0/track',json={'content':'交付原型','confirmed':True,'owner':'甲'})
    assert result.json()=={'id':tid,'reused':True}
    task=c.get(f'/api/meetings/{mid}').json()['note_todos'][0]
    assert task['status']=='done' and task['owner']=='乙'


@pytest.mark.parametrize('audience', ['internal', 'external'])
def test_audience_exports_follow_preview_and_sending_is_explicit(setup, audience, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile
    from app.meeting_review import MeetingReview
    from app.routes import minutes

    c,store,mid,other,aid,doc=setup
    doc.title='INTERNAL-TITLE'
    doc.purpose='INTERNAL-PURPOSE'
    doc.overview=[MinutesItem(content='共享议题',source_segment_ids=['s1'])]
    store.execute('UPDATE artifacts SET payload=? WHERE id=?',(encode(doc.model_dump()),aid))
    review=MeetingReview(store)
    for step in ('transcript','speakers'):
        review.set(mid,step,review.read(mid)['steps'][step]['fingerprint'],'confirmed')
    base=f'/api/meetings/{mid}/minutes'
    options={'artifact_id':aid,'audience':audience,'include_citations':True,
             'selection':['overview:0'] if audience=='external' else []}
    preview=c.post(base+'/audience-preview',json=options).json()
    response=c.post(base+'/audience-finalize',json={**options,'fingerprint':preview['fingerprint'],'confirmed':True})
    assert response.status_code==201,response.text
    snapshot=response.json()
    did=snapshot['id']
    export=lambda fmt:c.get(f'{base}/export/{fmt}?delivery_id={did}')
    assert export('md').text==preview['markdown']
    with ZipFile(BytesIO(export('docx').content)) as word:
        text=word.read('word/document.xml').decode()
    assert '共享议题' in text
    for marker in ('INTERNAL-TITLE','INTERNAL-PURPOSE','周五交付。'):
        assert (marker in text)==(audience=='internal')
        assert (marker in preview['markdown'])==(audience=='internal')
    with monkeypatch.context() as patch:
        def unavailable(*args):raise RuntimeError('合成导出失败')
        patch.setattr(minutes,'minutes_pdf',unavailable)
        assert export('pdf').status_code==503
    pdf=export('pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    sent=f'{base}/deliveries/{did}/sent'
    latest=c.get(f'/api/meetings/{mid}').json()['deliveries'][0]
    assert latest['sent_at'] is None and latest['sent_status']=='unmarked'
    assert c.put(sent.replace(mid,other),json={'sent':True}).status_code==404
    marked=c.put(sent,json={'sent':True}).json()
    first=marked['sent_at']
    assert marked['sent_status']=='sent'
    assert first and c.put(sent,json={'sent':True}).json()['sent_at']==first
    assert export('md').text==preview['markdown']
    cleared=c.put(sent,json={'sent':False}).json()
    assert cleared['sent_at'] is None and cleared['sent_status']=='unmarked'
    store.save_revision(mid,[],'manual')
    assert export('md').text==preview['markdown']
