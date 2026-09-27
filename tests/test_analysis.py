import io,json,wave,zipfile
import pytest
from fastapi.testclient import TestClient
from app.analysis import AnalysisItem,MeetingAnalysis,AIMinutesBasicInfo,AIMinutesResult,chunk_segments,sanitize_citations,LMStudioProvider,analysis_chunk_chars,context_window_from_metadata
from app.db import uid,encode
from app.main import create_app
from app.minutes import MeetingMinutes,minutes_markdown


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as value:yield value


def upload(client):
    output=io.BytesIO()
    with wave.open(output,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*16000)
    response=client.post('/api/meetings',files={'file':('测试会议.wav',output.getvalue(),'audio/wav')})
    return response.json()['id']


def sample():return [{'id':'seg1','start':0,'end':.5,'text':'确定周五交付。','speaker':'发言人 1','boundary_review':False}]


class FakeEndpointProvider:
    def resolve_model(self,requested=None):return requested or 'local-model'


def test_analysis_status_explains_missing_token(client,monkeypatch):
    monkeypatch.delenv('LM_API_TOKEN',raising=False)
    status=client.get('/api/status').json()['analysis']
    assert not status['ready'] and 'LM_API_TOKEN' in status['error']


def test_analysis_job_binds_current_revision(client,monkeypatch):
    monkeypatch.setattr('app.main.LMStudioProvider',FakeEndpointProvider)
    mid=upload(client);client.app.state.store.save_revision(mid,sample(),'manual',expected=0)
    response=client.post(f'/api/meetings/{mid}/analysis-jobs',json={'model':'local-model'})
    assert response.status_code==201
    job=client.app.state.store.one('SELECT * FROM jobs WHERE id=?',(response.json()['id'],))
    assert job['kind']=='analysis'
    assert json.loads(job['config'])['revision']==1


def test_analysis_worker_checkpoints_citations_staleness_and_export(client,monkeypatch):
    class FakeProvider:
        def models(self):return ['local-model']
        def analyze_chunk(self,model,segments,work,run):
            sid=segments[0]['id']
            return MeetingAnalysis(summary=[AnalysisItem(content='确定周五交付',source_segment_ids=[sid])],
                decisions=[AnalysisItem(content='按计划交付',source_segment_ids=[sid])],
                action_items=[AnalysisItem(content='完成交付',source_segment_ids=[sid],owner='发言人 1',due_date='周五')])
        def reduce(self,*_):raise AssertionError('单块不应归并')
    monkeypatch.setattr('app.worker.LMStudioProvider',FakeProvider)
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    jid=uid();config={'revision':1,'model':'local-model','template_version':'test','max_chars':8000}
    store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)',(jid,mid,'running','test',encode(config),'analysis'))
    client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    detail=client.get(f'/api/meetings/{mid}').json();artifact=detail['artifacts'][0]
    assert artifact['kind']=='meeting_analysis' and not artifact['stale']
    assert artifact['provenance']['model']=='local-model'
    markdown=client.get(f'/api/meetings/{mid}/analysis/export/md')
    assert markdown.status_code==200 and '确定周五交付' in markdown.text and '依据：00:00' in markdown.text
    assert (store.directory(mid)/jid/'map-0000.json').is_file()
    store.save_revision(mid,sample(),'manual',expected=1)
    assert client.get(f'/api/meetings/{mid}').json()['artifacts'][0]['stale']
    assert client.get(f'/api/meetings/{mid}/analysis/export/md').status_code==404


def test_analysis_rejects_model_citation_outside_input(client,monkeypatch):
    class BadProvider:
        def models(self):return ['local-model']
        def analyze_chunk(self,*_):return MeetingAnalysis(summary=[AnalysisItem(content='虚构',source_segment_ids=['missing'])])
    monkeypatch.setattr('app.worker.LMStudioProvider',BadProvider)
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    jid=uid();store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config,kind) VALUES(?,?,?,?,?,?)',
        (jid,mid,'running','test',encode({'revision':1,'model':'local-model'}),'analysis'))
    with pytest.raises(ValueError,match='不存在'):client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))


def test_analysis_chunking_preserves_every_segment():
    segments=[dict(sample()[0],id=f's{i}',text='字'*20) for i in range(10)]
    chunks=chunk_segments(segments,max_chars=250)
    assert len(chunks)>1
    assert [s['id'] for chunk in chunks for s in chunk]==[s['id'] for s in segments]


def test_analysis_chunk_budget_scales_with_context_window():
    assert analysis_chunk_chars(32768)==8000
    assert 20000<analysis_chunk_chars(65536)<30000
    assert analysis_chunk_chars(262144)==48000
    assert analysis_chunk_chars(524288)==64000
    assert analysis_chunk_chars(1_000_000)==96000
    assert analysis_chunk_chars(1_000_000,'quality')==48000
    assert analysis_chunk_chars(1_000_000,'speed')==96000
    assert analysis_chunk_chars(1_000_000,'custom',120000)==120000
    with pytest.raises(ValueError,match='安全预算'):analysis_chunk_chars(32768,'custom',120000)
    assert context_window_from_metadata({'id':'x','max_context_length':262144})==262144
    assert context_window_from_metadata({'id':'x','capabilities':{'context_window':131072}})==131072
    assert context_window_from_metadata({'id':'x'}) is None


def test_analysis_schema_restricts_and_sanitizes_citations():
    schema=LMStudioProvider._response_format({'seg1','seg2'})
    ids=schema['json_schema']['schema']['properties']['summary']['items']['properties']['source_segment_ids']['items']['enum']
    assert ids==['seg1','seg2']
    raw=MeetingAnalysis(
        summary=[AnalysisItem(content='保留',source_segment_ids=['seg1','missing','seg1'])],
        decisions=[AnalysisItem(content='删除',source_segment_ids=['missing'])])
    result=sanitize_citations(raw,{'seg1','seg2'})
    assert result.summary[0].source_segment_ids==['seg1']
    assert result.decisions==[]


def test_lmstudio_response_accepts_json_code_fence():
    from scripts.lmstudio_analyze import extract_json
    result=extract_json({'choices':[{'message':{'content':'```json\n{"summary": []}\n```'}}]})
    assert result=={'summary':[]}


def test_analysis_json_retry_recovers_from_truncated_response(monkeypatch):
    from scripts import lmstudio_analyze
    sections={'summary':[],'key_points':[],'decisions':[],'action_items':[],'open_questions':[]}
    responses=[
        {'choices':[{'finish_reason':'stop','message':{'content':'{"summary":[{"content":"截断'}}]},
        {'choices':[{'finish_reason':'stop','message':{'content':'说明文字\n'+json.dumps(sections,ensure_ascii=False)}}]},
    ]
    monkeypatch.setattr(lmstudio_analyze,'request_once',lambda *_:responses.pop(0))
    result=lmstudio_analyze.complete_json('http://localhost/v1/chat/completions',{},
        {'messages':[],'max_tokens':4096,'response_format':{'type':'json_object'}})
    assert result==sections


def test_analysis_json_reads_reasoning_field_and_downgrades_format(monkeypatch):
    from scripts import lmstudio_analyze
    sections={'summary':[],'key_points':[],'decisions':[],'action_items':[],'open_questions':[]}
    payloads=[]
    responses=[
        {'choices':[{'finish_reason':'stop','message':{'content':''}}]},
        {'choices':[{'finish_reason':'stop','message':{'content':'','reasoning_content':json.dumps(sections)}}]},
    ]
    def fake_request(_url,_headers,payload):
        payloads.append(payload);return responses.pop(0)
    monkeypatch.setattr(lmstudio_analyze,'request_once',fake_request)
    result=lmstudio_analyze.complete_json('http://localhost/v1/chat/completions',{},
        {'messages':[],'max_tokens':4096,'response_format':{'type':'json_schema','json_schema':{}}})
    assert result==sections
    assert payloads[0]['response_format']['type']=='json_schema'
    assert payloads[1]['response_format']['type']=='json_object'


def test_llm_config_crud_hides_secret_and_binds_analysis_job(client):
    local={'name':'本机模型','provider_type':'local','base_url':'http://127.0.0.1:1234/v1',
        'model':'qwen-local','api_key':'local-secret','is_default':True,'context_window':262144}
    created=client.post('/api/llm-configs',json=local)
    assert created.status_code==201
    profile=created.json()
    assert profile['has_api_key'] and 'api_key' not in profile
    assert 'local-secret' not in client.get('/api/llm-configs').text
    saved=client.app.state.store.one('SELECT * FROM llm_configs WHERE id=?',(profile['id'],))
    assert saved['api_key']=='local-secret'
    assert saved['context_window']==262144 and profile['analysis_max_chars']==48000

    mid=upload(client);client.app.state.store.save_revision(mid,sample(),'manual',expected=0)
    response=client.post(f'/api/meetings/{mid}/analysis-jobs',json={'profile_id':profile['id']})
    assert response.status_code==201
    job=client.app.state.store.one('SELECT config FROM jobs WHERE id=?',(response.json()['id'],))
    config=json.loads(job['config'])
    assert config['profile_id']==profile['id'] and config['model']=='qwen-local'
    assert config['context_window']==262144 and config['max_chars']==48000
    assert 'local-secret' not in job['config']


def test_llm_config_rejects_unsafe_endpoints(client):
    base={'name':'bad','model':'x','api_key':'secret'}
    assert client.post('/api/llm-configs',json=base|{'provider_type':'local','base_url':'http://192.168.1.5:1234/v1'}).status_code==400
    assert client.post('/api/llm-configs',json=base|{'provider_type':'cloud','base_url':'http://api.example.com/v1'}).status_code==400


def test_minutes_generate_edit_and_export_word(client):
    from app.analysis import save_meeting_analysis
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    analysis=MeetingAnalysis(
        summary=[AnalysisItem(content='讨论交付安排',source_segment_ids=['seg1'])],
        key_points=[AnalysisItem(content='交付时间明确',source_segment_ids=['seg1'])],
        decisions=[AnalysisItem(content='周五交付',source_segment_ids=['seg1'])],
        action_items=[AnalysisItem(content='完成交付',source_segment_ids=['seg1'],owner='发言人 1',due_date='周五')])
    source_id=save_meeting_analysis(store,mid,1,analysis,{'model':'test'})
    generated=client.post(f'/api/meetings/{mid}/minutes',json={'style':'formal'})
    assert generated.status_code==201
    artifact=generated.json();document=artifact['payload']
    assert artifact['provenance']['source_analysis_id']==source_id
    assert document['action_items'][0]['owner']=='发言人 1'
    document['location']='第一会议室';document['title']='项目会议纪要'
    saved=client.put(f'/api/meetings/{mid}/minutes',json={'source_analysis_id':source_id,'document':document})
    assert saved.status_code==201 and saved.json()['provenance']['edited']
    markdown=client.get(f'/api/meetings/{mid}/minutes/export/md?include_citations=true')
    assert markdown.status_code==200 and '第一会议室' in markdown.text and '原文：00:00' in markdown.text
    word=client.get(f'/api/meetings/{mid}/minutes/export/docx?include_citations=true')
    assert word.status_code==200 and word.headers['content-type'].startswith('application/vnd.openxmlformats')
    with zipfile.ZipFile(io.BytesIO(word.content)) as archive:
        xml=archive.read('word/document.xml').decode()
        assert '项目会议纪要' in xml and '完成交付' in xml and '原文依据' in xml
        assert 'w:pgSz w:w="11906" w:h="16838"' in xml
    pdf=client.get(f'/api/meetings/{mid}/minutes/export/pdf?include_citations=true&inline=true')
    assert pdf.status_code==200 and pdf.headers['content-type']=='application/pdf'
    assert pdf.content.startswith(b'%PDF')
    assert pdf.headers['content-disposition'].startswith('inline;')
    assert pdf.headers['cache-control']=='no-store'


def test_minutes_require_current_analysis_and_reject_unknown_citation(client):
    from app.analysis import save_meeting_analysis
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    assert client.post(f'/api/meetings/{mid}/minutes',json={'style':'formal'}).status_code==409
    source_id=save_meeting_analysis(store,mid,1,MeetingAnalysis(summary=[AnalysisItem(content='摘要',source_segment_ids=['seg1'])]),{'model':'test'})
    document=client.post(f'/api/meetings/{mid}/minutes',json={'style':'brief'}).json()['payload']
    document['summary']=[]
    document['overview'][0]['source_segment_ids']=['missing']
    response=client.put(f'/api/meetings/{mid}/minutes',json={'source_analysis_id':source_id,'document':document})
    assert response.status_code==400 and '不存在' in response.text


def test_minutes_styles_have_distinct_structure_and_action_status(client):
    from app.analysis import save_meeting_analysis
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    items=[AnalysisItem(content=f'要点{i}',source_segment_ids=['seg1']) for i in range(5)]
    analysis=MeetingAnalysis(summary=items,key_points=items,decisions=items,action_items=items,open_questions=items)
    save_meeting_analysis(store,mid,1,analysis,{'model':'test'})

    formal=client.post(f'/api/meetings/{mid}/minutes',json={'style':'formal'}).json()['payload']
    brief=client.post(f'/api/meetings/{mid}/minutes',json={'style':'brief'}).json()['payload']
    internal=client.post(f'/api/meetings/{mid}/minutes',json={'style':'internal'}).json()['payload']
    assert formal['conclusions']==[] and len(formal['overview'])==2
    assert brief['conclusions']==[] and len(brief['overview'])==1 and len(brief['decisions'])==3
    assert len(internal['conclusions'])==5
    assert all(item['status']=='待开始' for item in formal['action_items'])

    document=MeetingMinutes.model_validate(formal)
    markdown=minutes_markdown(document,sample())
    assert '## 会议成果' in markdown and '| 决策事项 | 负责人 | 生效/完成时间 |' in markdown
    assert '| 行动项 | 负责人 | 截止时间 | 状态 |' in markdown
    assert '## 讨论要点' not in markdown


def test_llm_polish_minutes_keeps_citations_and_rejects_invented_fields(client,monkeypatch):
    from app.analysis import save_meeting_analysis
    class FakePolisher:
        base_url='http://127.0.0.1:1234/v1'
        def resolve_model(self,requested=None):return requested or 'polish-model'
        def polish_minutes(self,model,document,work,run):
            return MeetingAnalysis(
                summary=[AnalysisItem(content='已整理的会议结果',source_segment_ids=['seg1'])],
                decisions=[AnalysisItem(content='已整理的决策',source_segment_ids=['seg1'],owner='虚构负责人',due_date='虚构日期')],
                action_items=[AnalysisItem(content='已整理的行动',source_segment_ids=['seg1'],owner='发言人 1',due_date='周五')])
    monkeypatch.setattr('app.main.LMStudioProvider',FakePolisher)
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    source_id=save_meeting_analysis(store,mid,1,MeetingAnalysis(
        summary=[AnalysisItem(content='原摘要',source_segment_ids=['seg1'])],
        decisions=[AnalysisItem(content='原决策',source_segment_ids=['seg1'])],
        action_items=[AnalysisItem(content='原行动',source_segment_ids=['seg1'],owner='发言人 1',due_date='周五')]),{'model':'test'})
    client.post(f'/api/meetings/{mid}/minutes',json={'style':'formal'})

    response=client.post(f'/api/meetings/{mid}/minutes/polish',json={'model':'polish-model'})
    assert response.status_code==201
    artifact=response.json();document=artifact['payload']
    assert artifact['provenance']['llm_polished'] and artifact['provenance']['source_analysis_id']==source_id
    assert document['overview'][0]['content']=='已整理的会议结果'
    assert document['decisions'][0]['owner'] is None and document['decisions'][0]['due_date'] is None
    assert document['action_items'][0]['owner']=='发言人 1' and document['action_items'][0]['status']=='待开始'
    assert document['action_items'][0]['source_segment_ids']==['seg1']


def test_ai_minutes_are_generated_directly_from_transcript_and_can_be_edited(client,monkeypatch):
    class FakeAIMinutesProvider:
        base_url='http://127.0.0.1:1234/v1'
        def resolve_model(self,requested=None):return requested or 'minutes-model'
        def generate_ai_minutes(self,model,segments,metadata,work,run):
            assert segments[0]['text']=='确定周五交付。'
            assert metadata['title']=='测试会议'
            return AIMinutesResult(
                basic_info=AIMinutesBasicInfo(topic='项目交付会',meeting_form='线上会议',attendees=['发言人 1'],purpose='确认交付安排',source_segment_ids=['seg1']),
                core_content=[AnalysisItem(content='讨论交付安排',source_segment_ids=['seg1'])],
                action_items=[AnalysisItem(content='完成交付',source_segment_ids=['seg1'],owner='发言人 1',due_date='周五')],
                summary=[AnalysisItem(content='交付安排已明确',source_segment_ids=['seg1'])])
    monkeypatch.setattr('app.main.LMStudioProvider',FakeAIMinutesProvider)
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)

    response=client.post(f'/api/meetings/{mid}/minutes/ai',json={'model':'minutes-model'})
    assert response.status_code==201
    artifact=response.json();document=artifact['payload']
    assert artifact['provenance']['ai_generated'] and artifact['provenance']['source_transcript_revision']==1
    assert artifact['provenance']['generation_mode']=='quick'
    assert 'source_analysis_id' not in artifact['provenance']
    assert document['style']=='ai' and document['meeting_form']=='线上会议'
    assert document['title']=='项目交付会会议纪要' and document['attendees']=='发言人 1'
    assert document['overview'][0]['content']=='讨论交付安排'
    assert document['action_items'][0]['status']=='待开始'
    assert document['conclusions'][0]['content']=='交付安排已明确'
    detail=client.get(f'/api/meetings/{mid}').json()
    assert not any(item['kind']=='meeting_analysis' for item in detail['artifacts'])

    document['purpose']='确认最终交付安排'
    saved=client.put(f'/api/meetings/{mid}/minutes',json={'source_analysis_id':None,'document':document})
    assert saved.status_code==201 and saved.json()['provenance']['ai_generated']


def test_full_ai_minutes_require_and_bind_current_analysis(client,monkeypatch):
    from app.analysis import save_meeting_analysis
    class FakeAIMinutesProvider:
        base_url='http://127.0.0.1:1234/v1'
        def resolve_model(self,requested=None):return requested or 'minutes-model'
        def generate_ai_minutes(self,model,segments,metadata,work,run):
            return AIMinutesResult(
                basic_info=AIMinutesBasicInfo(topic='项目会',attendees=['发言人 1'],source_segment_ids=['seg1']),
                core_content=[AnalysisItem(content='讨论交付',source_segment_ids=['seg1'])],
                summary=[AnalysisItem(content='形成结论',source_segment_ids=['seg1'])])
    monkeypatch.setattr('app.main.LMStudioProvider',FakeAIMinutesProvider)
    mid=upload(client);store=client.app.state.store;store.save_revision(mid,sample(),'manual',expected=0)
    assert client.post(f'/api/meetings/{mid}/minutes/ai',json={'model':'minutes-model','mode':'full'}).status_code==409
    source_id=save_meeting_analysis(store,mid,1,MeetingAnalysis(summary=[AnalysisItem(content='摘要',source_segment_ids=['seg1'])]),{'model':'test'})
    response=client.post(f'/api/meetings/{mid}/minutes/ai',json={'model':'minutes-model','mode':'full'})
    assert response.status_code==201
    provenance=response.json()['provenance']
    assert provenance['generation_mode']=='full' and provenance['source_analysis_id']==source_id
