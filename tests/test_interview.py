import json
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid,encode
from app.analysis import AIMinutesResult,AIMinutesBasicInfo,LMStudioProvider
from app.interview import InterviewResult,response_schema,project
from app.minutes import minutes_markdown,MeetingMinutes
from app.minutes_composition import build_from_ai,CompositionSettings,semantic_template

SEGMENTS=[{'id':'q','start':0,'end':5,'speaker':'面试官','text':'请介绍你本人在支付改造项目中的职责。'},
 {'id':'a','start':5,'end':20,'speaker':'候选人','text':'我负责接口实现，我们团队把延迟降低了百分之三十。具体基线我需要补充。'}]
def result():
 e=[{'segment_id':'a','quote':'我负责接口实现'}]
 return AIMinutesResult(basic_info=AIMinutesBasicInfo(topic='后端岗位面试',source_segment_ids=['q']),interview=InterviewResult.model_validate({
  'background':[{'content':'候选人自述负责支付接口实现。','evidence':e}],
  'qa':[{'question':'本人负责什么工作？','content':'候选人表示负责接口实现，团队降低了延迟，但基线需补充。','evidence':e}],
  'competencies':[{'dimension':'项目贡献','coverage':'covered','content':'已获得本人承担接口实现的回答，其他职责边界待核对。','evidence':e},{'dimension':'团队管理','coverage':'not_discussed','content':'本轮未找到相关回答。','evidence':[]}],
  'concerns':[{'content':'延迟改善的测量基线尚不明确。','reason':'候选人明确表示具体基线需补充。','followup':'请补充改造前后的测量基线及测试方法。','evidence':[{'segment_id':'a','quote':'具体基线我需要补充'}]}],
  'followups':[], 'summary':[{'content':'候选人陈述了接口实现经历，量化结果的测量方法仍待核对。','evidence':e}]}))
@pytest.fixture
def setup(tmp_path,monkeypatch):
 app=create_app(tmp_path,start_worker=False)
 with TestClient(app) as c:
  store=app.state.store;mid=uid();store.directory(mid).mkdir();store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'合成面试','test.wav'));store.save_revision(mid,SEGMENTS,'manual')
  seen=[]
  class Fake:
   base_url='http://127.0.0.1:1234/v1'
   def resolve_model(self,model=None):return 'synthetic'
   def generate_ai_minutes(self,model,segments,metadata,work,run):seen.append(metadata);return result()
  monkeypatch.setattr('app.main.LMStudioProvider',Fake)
  yield c,store,mid,seen

def generate(c,mid):
 response=c.post(f'/api/meetings/{mid}/minutes/compose',json={'template_id':'online-interview','method':'ai','audience':'internal'})
 assert response.status_code==201,response.text
 return response.json()['artifact']
def save(c,mid,artifact,**updates):
 response=c.put(f'/api/meetings/{mid}/minutes',json={'source_minutes_id':artifact['id'],'allow_branch':True,'document':{**artifact['payload'],**updates}})
 assert response.status_code==201,response.text
 return response.json()

def test_interview_preparation_and_private_notes_do_not_become_candidate_facts(setup):
 c,store,mid,seen=setup
 assert any(t['id']=='online-interview' for t in c.get('/api/minutes-templates').json())
 prep={'candidate':'候选人甲','role':'后端工程师','focus':'项目贡献、测量方法','job_description':'接口开发'}
 assert c.put(f'/api/meetings/{mid}/brief',json={'interview':prep}).status_code==200
 c.post(f'/api/meetings/{mid}/notes',json={'raw_text':'PRIVATE-SENTINEL','captured_at':5,'note_scope':'personal','note_type':'note'})
 artifact=generate(c,mid);doc=artifact['payload']
 assert doc['interview_context']['role']=='后端工程师'
 assert seen[0]['preparation']['interview']['focus']==prep['focus'] and 'PRIVATE-SENTINEL' not in encode(seen)
 assert doc['open_questions']==[] and doc['interviewer_evaluation']==''
 assert '面试面试' not in doc['title']
 assert '面试准备（人工填写）' in doc['overview'][0]['content']
 assert '面试官评价' not in encode(doc['summary'])
 c.put(f'/api/meetings/{mid}/brief',json={'interview':{'role':'更新的岗位'}})
 assert not generate(c,mid)['payload']['interview_evaluation_needs_review']


def test_concern_review_exports_only_kept_items_and_saved_human_evaluation_survives_regeneration(setup):
 c,store,mid,seen=setup;first=generate(c,mid)
 base=f'/api/meetings/{mid}/minutes/export/md?artifact_id='
 assert '建议追问' not in c.get(base+first['id']).text
 kept=save(c,mid,first,interview_reviews={'0':'kept'},interviewer_evaluation='人工评价：需要补充具体量化例证。')
 assert '建议追问' in c.get(base+kept['id']).text
 assert '人工评价：需要补充' in c.get(base+kept['id']).text
 regenerated=generate(c,mid)
 assert regenerated['payload']['interviewer_evaluation']==kept['payload']['interviewer_evaluation']
 assert regenerated['payload']['interview_reviews']=={'0':'kept'}
 assert '人工评价：需要补充具体量化例证。' not in encode(seen)
 assert c.get(f'/api/meetings/{mid}').json()['active_minutes_id']==kept['id']
 ignored=save(c,mid,kept,interview_reviews={'0':'ignored'})
 assert '建议追问' not in c.get(base+ignored['id']).text
 assert c.get(base+kept['id']).text.count('建议追问')==1
 assert c.get(f'/api/meetings/{mid}/minutes/export/docx?artifact_id={kept["id"]}').content[:2]==b'PK'
 pdf=c.get(f'/api/meetings/{mid}/minutes/export/pdf?artifact_id={kept["id"]}')
 assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')


def test_context_change_marks_human_evaluation_for_recheck_and_preserves_old_version(setup):
 c,store,mid,_=setup;first=generate(c,mid);saved=save(c,mid,first,interviewer_evaluation='人工判断')
 c.put(f'/api/meetings/{mid}/brief',json={'interview':{'role':'新岗位'}})
 new=generate(c,mid);assert new['payload']['interviewer_evaluation']=='人工判断'
 assert new['payload']['interview_evaluation_needs_review']
 assert '人工判断' not in encode(new['payload']['summary'])
 assert c.post(f'/api/meetings/{mid}/minutes/{new["id"]}/adopt').status_code==200
 assert c.post(f'/api/meetings/{mid}/minutes/deliveries',json={'artifact_id':new['id'],'confirmed':True}).status_code==409
 reviewed=save(c,mid,new,interview_evaluation_needs_review=False)
 assert '人工判断' in encode(reviewed['payload']['summary'])
 assert c.post(f'/api/meetings/{mid}/minutes/deliveries',json={'artifact_id':reviewed['id'],'confirmed':True}).status_code==201


def test_rejects_invalid_quotes_and_generic_analysis_for_interview(setup):
 c,store,mid,_=setup;first=generate(c,mid);doc=first['payload'];doc['interview']['qa'][0]['evidence'][0]['quote']='这句话不存在'
 assert c.put(f'/api/meetings/{mid}/minutes',json={'source_minutes_id':first['id'],'document':doc}).status_code==400
 assert c.post(f'/api/meetings/{mid}/minutes/compose',json={'template_id':'general-meeting','method':'current','source_minutes_id':first['id']}).status_code==409
 for method in ('analysis','current'):
  assert c.post(f'/api/meetings/{mid}/minutes/compose',json={'template_id':'online-interview','method':method,'source_minutes_id':first['id']}).status_code==409


def test_interview_excluded_from_general_retrieval_and_old_sources_expire(setup):
 c,store,mid,_=setup
 from app.retrieval import Retrieval
 from app.meeting_flow import ai_context
 with store.connect() as db:
  before=ai_context(db,mid)[1];assert Retrieval(store).units(db)
 c.put(f'/api/meetings/{mid}/brief',json={'title':'合成面试'})
 with store.connect() as db:assert ai_context(db,mid)[1]!=before # title really changed preparation
 c.put(f'/api/meetings/{mid}/brief',json={'interview':{'role':'后端'}})
 with store.connect() as db:assert not Retrieval(store).units(db)
 generate(c,mid)
 c.put(f'/api/meetings/{mid}/brief',json={})
 with store.connect() as db:assert not Retrieval(store).units(db) # template itself also identifies restricted scope


def test_provider_uses_dedicated_schema_exact_quotes_and_aliases(tmp_path):
 provider=LMStudioProvider();expected=result();seen={}
 def run(args,extra):
  request_path=tmp_path/'result.request.json';payload=json.loads(request_path.read_text());seen.update(payload)
  value=expected.model_dump();value['basic_info']['source_segment_ids']=['s1']
  for e in value['interview'].values():
   for item in e:
    for ev in item['evidence']:ev['segment_id']='s2'
  (tmp_path/'result.response.json').write_text(encode(value))
 actual=provider.generate_ai_minutes('synthetic',SEGMENTS,{'minutes_template':semantic_template('online-interview').model_dump(),'preparation':{'interview':{'role':'后端'}}},tmp_path/'result',run)
 assert actual.interview.qa[0].evidence[0].segment_id=='a'
 schema=seen['response_format']['json_schema'];assert schema['name']=='interview_minutes'
 assert schema['schema']['$defs']['Evidence']['properties']['segment_id']['enum']==['s1','s2']
 assert 'interviewer_evaluation' not in encode(schema)
 assert '评分、排名、录用或淘汰建议' in seen['messages'][0]['content']
 bad=result();bad.interview.qa[0].evidence[0].quote='不存在的摘录'
 with pytest.raises(ValueError):bad.interview.validate_sources(SEGMENTS)
