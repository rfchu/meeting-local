import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Store,uid,encode
from app.meeting_review import MeetingReview
from app.notes import insert_note

@pytest.fixture
def review(tmp_path):
    store=Store(tmp_path);mid=uid()
    store.execute('INSERT INTO meetings(id,title,filename,duration) VALUES(?,?,?,?)',(mid,'测试会议','test.wav',20))
    return store,mid,MeetingReview(store)

def transcript(store,mid):
    return store.save_revision(mid,[{'id':'seg1','text':'周五交付','speaker':'发言人 1','start':0,'end':10,'boundary_review':False}],'manual')

def mark(tracker,mid,step,decision='confirmed'):
    return tracker.set(mid,step,tracker.read(mid)['steps'][step]['fingerprint'],decision)

def minutes(store,mid,provenance=None):
    aid=uid();revision=store.one('SELECT revision FROM meetings WHERE id=?',(mid,))['revision']
    store.execute('INSERT INTO artifacts(id,meeting_id,revision,kind,payload,provenance) VALUES(?,?,?,?,?,?)',
                  (aid,mid,revision,'meeting_minutes',encode({'title':'测试纪要'}),encode(provenance or {})))
    return aid

def test_manual_progress_persists_and_does_not_auto_confirm_ai_output(review):
    store,mid,tracker=review
    assert tracker.read(mid)['steps']['transcript']['state']=='blocked'
    with pytest.raises(ValueError):mark(tracker,mid,'transcript')
    transcript(store,mid);minutes(store,mid)
    assert tracker.read(mid)['steps']['minutes']['state']=='pending'
    mark(tracker,mid,'transcript');mark(tracker,mid,'speakers','skipped');mark(tracker,mid,'minutes')
    restored=MeetingReview(Store(store.root)).read(mid)
    assert restored['steps']['speakers']['decision']=='skipped'
    assert restored['steps']['notes']['state']=='not_needed'
    assert restored['steps']['minutes']['state']=='done'
    tracker.reset(mid,'minutes');assert tracker.read(mid)['steps']['minutes']['state']=='pending'

def test_revision_change_invalidates_confirmations_and_rejects_old_page(review):
    store,mid,tracker=review;transcript(store,mid)
    old=mark(tracker,mid,'transcript')['steps']['transcript']['fingerprint']
    mark(tracker,mid,'speakers','skipped');transcript(store,mid)
    assert tracker.read(mid)['steps']['transcript']['state']=='stale'
    assert tracker.read(mid)['steps']['speakers']['state']=='stale'
    with pytest.raises(ValueError,match='已经变化'):tracker.set(mid,'transcript',old,'confirmed')
    mark(tracker,mid,'transcript');assert tracker.read(mid)['steps']['transcript']['state']=='done'

def test_notes_change_and_adopted_source_change_invalidate_related_steps(review):
    store,mid,tracker=review;transcript(store,mid)
    with store.connect() as db:
        nid=insert_note(db,mid,{'raw_text':'交付安排','captured_at':2,'note_type':'note','note_scope':'meeting'})
    minutes(store,mid,{'adopted_notes':[{'note_id':nid}]})
    mark(tracker,mid,'notes');mark(tracker,mid,'minutes');mark(tracker,mid,'transcript')
    store.execute('UPDATE meeting_notes SET note_scope=? WHERE id=?',('personal',nid))
    progress=tracker.read(mid)
    assert progress['steps']['notes']['state']=='stale'
    assert progress['steps']['minutes']['state']=='stale'
    assert progress['steps']['transcript']['state']=='done'
    store.execute('DELETE FROM meeting_notes WHERE id=?',(nid,))
    assert tracker.read(mid)['steps']['notes']['state']=='not_needed'

def test_new_minutes_and_title_changes_require_recheck(review):
    store,mid,tracker=review;transcript(store,mid);minutes(store,mid)
    mark(tracker,mid,'minutes');mark(tracker,mid,'transcript');mark(tracker,mid,'speakers')
    minutes(store,mid)
    assert tracker.read(mid)['steps']['minutes']['state']=='stale'
    assert tracker.read(mid)['steps']['transcript']['state']=='done'
    store.execute('UPDATE meetings SET title=? WHERE id=?',('新主题',mid))
    assert tracker.read(mid)['steps']['transcript']['state']=='stale'
    assert tracker.read(mid)['steps']['speakers']['state']=='done'

def test_api_validates_step_decision_conflicts_and_cleanup(review):
    store,mid,_=review;transcript(store,mid)
    with TestClient(create_app(store.root,start_worker=False)) as client:
        state=client.get('/api/meetings/'+mid).json()['review']
        url=f'/api/meetings/{mid}/review/transcript'
        body={'fingerprint':state['steps']['transcript']['fingerprint']}
        assert client.put(url,json=body).json()['steps']['transcript']['state']=='done'
        assert client.put(url,json={**body,'decision':'skipped'}).status_code==409
        assert client.put(url,json={'fingerprint':'bad'}).status_code==422
        assert client.put(f'/api/meetings/{mid}/review/invalid',json=body).status_code==422
        assert client.delete(url).json()['steps']['transcript']['state']=='pending'
        client.put(url,json=body)
        assert client.delete('/api/meetings/'+mid).status_code==200
        assert not store.all('SELECT * FROM meeting_reviews WHERE meeting_id=?',(mid,))
        assert client.put(url,json=body).status_code==404
