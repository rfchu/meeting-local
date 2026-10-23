"""Editorial workflow invariants, using synthetic providers and isolated SQLite."""
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.analysis import AIMinutesResult,AIMinutesBasicInfo,AnalysisItem,MeetingAnalysis,LMStudioProvider
from app.minutes import MeetingMinutes,MinutesItem
from app.minutes_composition import CompositionSettings,build_from_ai,publish_analysis,canonical_source
from app.minutes_templates import builtin_template
from app.worker import Cancelled

SID='synthetic-real-uuid'
def item(text,**kw):return AnalysisItem(content=text,source_segment_ids=[SID],**kw)
def result():return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='交付核对',source_segment_ids=[SID]),overview=[item('核对本周安排')],core_content=[item('已完成验收材料')],decisions=[item('按周五排期推进')],action_items=[item('交付材料',owner='甲',due_date='周五')],open_questions=[item('收件人待确认')],summary=[item('形成交付安排')])

@pytest.fixture
def setup(tmp_path,monkeypatch):
    class Fake:
        base_url='http://localhost:1234/v1'
        def models(self):return ['synthetic']
        def resolve_model(self,*args):return 'synthetic'
        def generate_ai_minutes(self,model,segments,metadata,work,run):
            assert metadata['minutes_template']['id']==metadata['composition']['template_id']
            return result()
        def analyze_chunk(self,*args):return MeetingAnalysis(summary=[item('分析摘要')],key_points=[item('讨论进展')],decisions=[item('已定安排')],action_items=[item('交付材料',owner='甲',due_date='周五')])
    monkeypatch.setattr('app.main.LMStudioProvider',Fake)
    monkeypatch.setattr('app.worker.LMStudioProvider',Fake)
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;mid=uid();store.directory(mid).mkdir()
        store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成会议','test.wav'))
        store.save_revision(mid,[{'id':SID,'start':0,'end':5,'speaker':'甲','text':'材料完成了，周五由甲交付，收件人待确认。'}],'manual')
        yield client,store,mid

def compose(client,mid,**kw):
    response=client.post(f'/api/meetings/{mid}/minutes/compose',json={'model':'synthetic',**kw})
    assert response.status_code==201,response.text
    return response.json()

def test_generation_stays_draft_until_explicit_adoption(setup):
    c,s,m=setup
    first=compose(c,m)['artifact'];second=compose(c,m,template_id='project-weekly',detail_level='brief')['artifact']
    detail=c.get(f'/api/meetings/{m}').json()
    assert detail['active_minutes_id']==first['id']
    assert second['provenance']['draft'] is True
    assert second['payload']['template_id']=='project-weekly'
    sources={x['source']:x['title'] for x in second['payload']['template_snapshot']['sections']}
    assert sources['conclusions']=='本期工作进展' and sources['summary']=='会议总结'
    assert second['payload']['conclusions'][0]['content']=='已完成验收材料'
    assert second['payload']['summary'][0]['content']=='形成交付安排'
    assert c.post(f'/api/meetings/{m}/minutes/{second["id"]}/adopt').status_code==200
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==second['id']


def save(c,m,artifact,title):
    document={**artifact['payload'],'title':title}
    response=c.put(f'/api/meetings/{m}/minutes',json={'document':document,'source_minutes_id':artifact['id'],'allow_branch':True})
    assert response.status_code==201,response.text
    return response.json()

def test_historical_edits_are_branches_and_exports_use_selected_version(setup):
    c,s,m=setup;first=compose(c,m)['artifact'];second=compose(c,m)['artifact']
    branch=save(c,m,second,'未采用草稿修订')
    assert branch['provenance']['parent_minutes_id']==second['id'] and branch['provenance']['draft']
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==first['id']
    edited=save(c,m,first,'采用稿人工校对')
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==edited['id']
    assert '采用稿人工校对' in c.get(f'/api/meetings/{m}/minutes/export/md').text
    explicit=c.get(f'/api/meetings/{m}/minutes/export/md',params={'artifact_id':branch['id']})
    assert '未采用草稿修订' in explicit.text and '采用稿人工校对' not in explicit.text
    # Historical exports retain their own transcript for citations after revision changes.
    s.save_revision(m,[{'id':'new-id','start':0,'end':2,'speaker':'甲','text':'更新后的原文'}],'manual')
    assert c.get(f'/api/meetings/{m}/minutes/export/md',params={'artifact_id':branch['id'],'include_citations':True}).status_code==200
    assert c.post(f'/api/meetings/{m}/minutes/{branch["id"]}/adopt').status_code==409


def test_cross_meeting_source_and_export_are_rejected(setup):
    c,s,m=setup;a=compose(c,m)['artifact'];other=uid();s.directory(other).mkdir()
    s.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(other,'其他会议','x.wav'))
    assert c.get(f'/api/meetings/{other}/minutes/export/md',params={'artifact_id':a['id']}).status_code==404
    assert c.post(f'/api/meetings/{other}/minutes/{a["id"]}/adopt').status_code==404
    assert c.post(f'/api/meetings/{other}/minutes/compose',json={'method':'current','source_minutes_id':a['id']}).status_code==404


def test_derived_brief_preserves_commitments_and_manual_facts(setup,monkeypatch):
    c,s,m=setup;a=compose(c,m)['artifact']
    document=a['payload'];document['action_items'][0]['status']='进行中';document['overview'].append({'content':'人工补充：保留纸质签收单','source_segment_ids':[]})
    saved=c.put(f'/api/meetings/{m}/minutes',json={'document':document,'source_minutes_id':a['id'],'allow_branch':True}).json()
    class Rewrite:
        def resolve_model(self,*args):return 'synthetic'
        def generate_ai_minutes(self,model,segments,metadata,*args):
            assert '人工补充' not in encode(metadata['source_minutes'])
            assert {x['id'] for x in segments}=={SID}
            output=result();output.decisions=[];output.action_items=[];output.open_questions=[]
            return output
    monkeypatch.setattr('app.main.LMStudioProvider',Rewrite)
    derived=compose(c,m,method='current',detail_level='brief',source_minutes_id=saved['id'])['artifact']
    for field in ('decisions','action_items','open_questions'):
        assert derived['payload'][field]==saved['payload'][field]
    assert any(x['content']=='人工补充：保留纸质签收单' for x in derived['payload']['overview'])
    assert derived['provenance']['source_minutes_dependencies']==[saved['id']]
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==saved['id']


def test_analysis_followup_survives_restart_and_does_not_truncate(setup):
    c,s,m=setup
    plan=compose(c,m,method='analysis',detail_level='brief',template_id='project-weekly')
    jid=plan['job_id'];config=json.loads(s.one('SELECT config FROM jobs WHERE id=?',(jid,))['config'])
    assert config['minutes_followup']['settings']['detail_level']=='brief'
    assert config['minutes_followup']['template']['id']=='project-weekly'
    with TestClient(create_app(s.root,start_worker=False)) as restarted:
        s.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
        restarted.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(jid,)))
        detail=restarted.get(f'/api/meetings/{m}').json()
        minutes=next(a for a in detail['artifacts'] if a['kind']=='meeting_minutes')
        assert minutes['id']==detail['active_minutes_id']
        assert minutes['payload']['conclusions'][0]['content']=='讨论进展'
        assert minutes['payload']['action_items'][0]['owner']=='甲'
        analysis=next(a for a in detail['artifacts'] if a['kind']=='meeting_analysis')
        payload=MeetingAnalysis.model_validate(analysis['payload'])
        payload.action_items=[item(f'事项{i}') for i in range(25)]
        s.execute('UPDATE artifacts SET payload=? WHERE id=?',(encode(payload.model_dump()),analysis['id']))
        draft=compose(restarted,m,method='analysis',detail_level='brief')['artifact']
        assert len(draft['payload']['action_items'])==25
        assert restarted.get(f'/api/meetings/{m}').json()['active_minutes_id']==minutes['id']


def test_cancelled_or_stale_followup_cannot_publish(setup):
    c,s,m=setup;analysis=MeetingAnalysis(summary=[item('合成')]);jid=uid()
    s.execute('INSERT INTO jobs(id,meeting_id,state,config) VALUES(?,?,?,?)',(jid,m,'cancelled','{}'))
    with pytest.raises(Cancelled):publish_analysis(s,m,1,analysis,'analysis',CompositionSettings(method='analysis'),job_id=jid)
    s.save_revision(m,[{'id':SID,'start':0,'end':5,'speaker':'甲','text':'变更'}],'manual')
    with pytest.raises(ValueError,match='原文版本'):publish_analysis(s,m,1,analysis,'analysis',CompositionSettings(method='analysis'))
    assert not s.all("SELECT id FROM artifacts WHERE kind='meeting_minutes'")


def test_active_custom_template_snapshot_is_frozen(setup):
    c,s,m=setup;t=builtin_template('project-weekly');t.id='custom';t.name='部门例会';t.version=7;t.builtin=False;t.sections[1].title='部门工作进展'
    s.execute('INSERT INTO minutes_templates(id,name,version,definition) VALUES(?,?,?,?)',(t.id,t.name,t.version,encode(t.model_dump())))
    draft=compose(c,m,template_id='custom')['artifact']
    assert draft['payload']['template_version']==7
    assert draft['payload']['template_snapshot']['sections'][1]['title']=='部门工作进展'
    s.execute("UPDATE minutes_templates SET status='inactive' WHERE id='custom'")
    assert c.post(f'/api/meetings/{m}/minutes/compose',json={'template_id':'custom'}).status_code==404
    assert '部门工作进展' in c.get(f'/api/meetings/{m}/minutes/export/md',params={'artifact_id':draft['id']}).text


def test_legacy_ai_mapping_keeps_discussion_and_summary_separate():
    source=MeetingMinutes(title='旧稿',style='ai',overview=[MinutesItem(content='核心讨论')],conclusions=[MinutesItem(content='会议总结')])
    mapped=canonical_source(source)
    assert mapped.conclusions[0].content=='核心讨论' and mapped.summary[0].content=='会议总结' and not mapped.overview
    assert source.overview[0].content=='核心讨论'


def test_composition_schema_written_before_request_and_source_aliases(tmp_path):
    provider=LMStudioProvider()
    def run(args,extra):
        request=json.loads(Path(args[args.index('--request')+1]).read_text())
        schema=request['response_format']['json_schema']['schema']
        assert {'overview','decisions','open_questions'}<=set(schema['required'])
        assert schema['properties']['action_items']['maxItems']==100
        assert SID not in request['messages'][-1]['content']
        output=result().model_dump()
        for key,values in output.items():
            if isinstance(values,list):
                for v in values:v['source_segment_ids']=['s1']
        output['basic_info']['source_segment_ids']=['s1']
        Path(args[args.index('--output')+1]).write_text(encode(output))
    out=provider.generate_ai_minutes('synthetic',[{'id':SID,'start':0,'end':5,'speaker':'甲','text':'合成'}],{'composition':{'detail_level':'standard'},'source_minutes':{'basic_info_source_segment_ids':[SID]},'minutes_template':{}},tmp_path/'request',run)
    assert out.summary[0].source_segment_ids==[SID]


def test_delete_removes_adoption_pointer(setup):
    c,s,m=setup;compose(c,m)
    assert c.delete(f'/api/meetings/{m}').status_code==200
    assert not s.one('SELECT * FROM minutes_selection WHERE meeting_id=?',(m,))


def test_new_draft_does_not_invalidate_review_or_followups(setup):
    from app.retrieval import Retrieval
    c,s,m=setup
    first=save(c,m,compose(c,m)['artifact'],'采用稿')
    before=c.get(f'/api/meetings/{m}').json()['review']
    fingerprint=before['steps']['minutes']['fingerprint']
    assert c.put(f'/api/meetings/{m}/review/minutes',json={'fingerprint':fingerprint,'decision':'confirmed'}).status_code==200
    second=save(c,m,compose(c,m)['artifact'],'未采用稿')
    detail=c.get(f'/api/meetings/{m}').json()
    assert detail['review']['steps']['minutes']['state']=='done'
    assert detail['review']['minutes_id']==first['id']
    with s.connect() as db:
        minutes_units=[json.loads(u[3]) for u in Retrieval(s).units(db) if json.loads(u[3])['kind']=='minutes']
    assert minutes_units and {u['artifact_id'] for u in minutes_units}=={first['id']}
    body={'confirmed':True,'content':'交付材料','owner':'甲','due_date':'周五'}
    assert c.post(f'/api/meetings/{m}/minutes/{first["id"]}/actions/0/track',json=body).status_code==201
    assert c.post(f'/api/meetings/{m}/minutes/{second["id"]}/actions/0/track',json=body).status_code==409


def test_derived_draft_inherits_invalidated_parent_evidence(setup):
    c,s,m=setup;first=compose(c,m)['artifact']
    derived=compose(c,m,method='current',source_minutes_id=first['id'])['artifact']
    # Simulate a parent whose explicitly adopted note has been withdrawn/deleted.
    provenance={**first['provenance'],'adopted_notes':[{'note_id':'withdrawn-note'}]}
    s.execute('UPDATE artifacts SET provenance=? WHERE id=?',(encode(provenance),first['id']))
    detail=c.get(f'/api/meetings/{m}').json()
    assert next(a for a in detail['artifacts'] if a['id']==derived['id'])['stale']
    assert c.post(f'/api/meetings/{m}/minutes/{derived["id"]}/adopt').status_code==409


def test_optional_analysis_is_enqueued_by_server_and_never_overwrites_minutes(setup):
    c,s,m=setup
    response=compose(c,m,also_analyze=True)
    jid=response['analysis_job_id'];aid=response['artifact']['id']
    assert s.one('SELECT state FROM jobs WHERE id=?',(jid,))['state']=='queued'
    with TestClient(create_app(s.root,start_worker=False)) as restarted:
        s.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
        restarted.app.state.worker.process(s.one('SELECT * FROM jobs WHERE id=?',(jid,)))
        detail=restarted.get(f'/api/meetings/{m}').json()
        assert detail['active_minutes_id']==aid
        assert sum(a['kind']=='meeting_minutes' for a in detail['artifacts'])==1
        again=compose(restarted,m,also_analyze=True)
        assert 'analysis_job_id' not in again  # Reuse current analysis rather than duplicate work.


def test_optional_analysis_failure_retains_successfully_saved_minutes(setup):
    c,s,m=setup
    s.execute('INSERT INTO jobs(id,meeting_id,state,config) VALUES(?,?,?,?)',(uid(),m,'queued','{}'))
    response=compose(c,m,also_analyze=True)
    assert '额外分析未启动' in response['warning']
    assert c.get(f'/api/meetings/{m}').json()['active_minutes_id']==response['artifact']['id']



def test_legacy_review_fingerprint_survives_adoption_schema_upgrade(setup):
    import hashlib
    from app.meeting_flow import brief
    c,s,m=setup
    artifact=compose(c,m)['artifact']
    with s.connect() as db:
        row=db.execute('SELECT title,revision FROM meetings WHERE id=?',(m,)).fetchone()
        old_artifact=dict(db.execute('SELECT id,payload,provenance FROM artifacts WHERE id=?',(artifact['id'],)).fetchone())
        old_inputs=[row['revision'],row['title'],old_artifact,[],[],brief(db,m)]
        old_fingerprint=hashlib.sha256(json.dumps(old_inputs,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    actual=c.get(f'/api/meetings/{m}').json()['review']['steps']['minutes']['fingerprint']
    assert actual==old_fingerprint
