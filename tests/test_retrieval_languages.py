"""Chinese/English retrieval, immutable QA language, and unchanged source quotations."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from app.db import uid,encode
from app.main import create_app
from app.qa import Answer,generate_answer,lexical_query
from app.retrieval import language_support


@pytest.fixture
def data(tmp_path,monkeypatch):
    monkeypatch.setattr('app.retrieval.embeddings',Mock(side_effect=AssertionError('Keyword retrieval must be model free')))
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app) as client:
        store=app.state.store;pid=uid()
        store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,context_window) VALUES(?,?,?,?,?,?)',
                      (pid,'Synthetic local','local','http://127.0.0.1:1234/v1','synthetic',32768))
        for text in ('The delivery deadline is Friday. Alice owns delivery.',
                     'Quarterly reports include artificial revenue examples.',
                     'The ART review is scheduled on Monday.',
                     'API接口在周五交付。', 'Ｚｅｂｒａ deployment is ready.'):
            mid=uid()
            store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'Fixture','synthetic.wav'))
            store.save_revision(mid,[{'id':'s1','start':0,'end':3,'speaker':'Alice','text':text}],'manual')
        yield client,store,app,pid


@pytest.mark.parametrize('query,expected',[
    ('art',['The ART review']),('ＺＥＢＲＡ',['Ｚｅｂｒａ deployment']),
    ('API',['API接口']),('交付',['API接口']),('delivery delivery',['The delivery deadline']),
])
def test_keywords_are_whole_english_words_and_keep_chinese_substrings(data,query,expected):
    client,store,_,_=data
    response=client.post('/api/retrieval/search',json={'query':query})
    assert response.status_code==200,response.text
    contents=[item['content'] for item in response.json()['results']]
    assert len(contents)==len(expected)
    assert all(any(prefix in text for text in contents) for prefix in expected)
    assert store.one('SELECT count(*) AS n FROM retrieval_settings')['n']==0


def test_english_questions_drop_function_words_and_preserve_bounded_chinese_terms():
    assert lexical_query('What is the delivery deadline, and who owns delivery?')=='delivery deadline owns'
    assert lexical_query('HOW is ＺＥＢＲＡ deployed?')=='zebra deployed'
    assert '交付' in lexical_query('项目交付时间是谁在什么时候决定的？')
    assert len(lexical_query('What '+'交付时间'*300))<=500


def test_question_language_does_not_limit_evidence_or_answer_language(data):
    client,_,_,pid=data
    for language in ('en','zh'):
        response=client.post('/api/retrieval/qa/prepare',json={
            'question':'What is the delivery deadline?', 'profile_id':pid,'output_language':language})
        assert response.status_code==201,response.text
        result=response.json()
        assert result['output_language']==language and result['sources']
        assert result['query']=='delivery deadline'
        assert all('delivery' in source['content'].lower() for source in result['sources'])
    response=client.post('/api/retrieval/qa/prepare',json={
        'question':'交付时间？','query':'delivery','profile_id':pid,'output_language':'zh'})
    assert 'The delivery deadline' in response.json()['sources'][0]['content']
    assert client.post('/api/retrieval/qa/prepare',json={
        'question':'test','profile_id':pid,'output_language':'auto'}).status_code==422


@pytest.mark.parametrize('language,legacy',[('en',False),('zh',True)])
def test_qa_task_and_history_retain_language_and_legacy_defaults(data,monkeypatch,language,legacy):
    client,store,app,pid=data
    run=client.post('/api/retrieval/qa/prepare',json={
        'question':'When is delivery?', 'query':'delivery','profile_id':pid,'output_language':language}).json()
    if legacy:
        row=store.one('SELECT config FROM qa_runs WHERE id=?',(run['id'],))
        config=json.loads(row['config']);config.pop('output_language')
        store.execute('UPDATE qa_runs SET config=? WHERE id=?',(encode(config),run['id']))
    seen=[]
    def fake(provider,model,question,sources,work,execute):
        seen.append(provider.output_language)
        return Answer(status='answered',claims=[{'text':'Friday' if language=='en' else '周五',
            'kind':'record','citations':[{'source':sources[0]['label'],'quote':'deadline is Friday'}]}])
    monkeypatch.setattr('app.qa.generate_answer',fake)
    queued=client.post(f"/api/retrieval/qa/{run['id']}/generate",json={}).json()
    jid=queued['job']['id'];store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    # Preparing another language does not mutate a previously submitted preview/task.
    client.post('/api/retrieval/qa/prepare',json={'question':'delivery','profile_id':pid,
                                             'output_language':'zh' if language=='en' else 'en'})
    app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    result=client.get(f"/api/retrieval/qa/{run['id']}").json()
    assert result['output_language']==language and seen==[language]
    assert result['answer']['claims'][0]['citations'][0]['quote']=='deadline is Friday'
    assert result['job']['state']=='completed'


def test_english_answer_prompt_keeps_chinese_quotes_and_rejects_translated_quote(tmp_path):
    sources=[{'label':'E1','content':'周五交付。','kind':'transcript'}]
    provider=SimpleNamespace(base_url='http://127.0.0.1:1234/v1',token='',output_language='en')
    captured=[];translated=False
    def execute(command,extra):
        request=json.loads(Path(command[command.index('--request')+1]).read_text())
        captured.append(request)
        output=Path(command[command.index('--output')+1])
        output.write_text(encode({'status':'answered','claims':[{'text':'Delivery is on Friday.',
            'kind':'record','citations':[{'source':'E1','quote':'Friday delivery' if translated else '周五交付'}]}]}))
    result=generate_answer(provider,'synthetic','When?',sources,tmp_path/'answer',execute)
    assert result.claims[0].citations[0].quote=='周五交付'
    assert 'claim text in English' in captured[0]['messages'][0]['content']
    assert 'never translate' in captured[0]['messages'][0]['content']
    translated=True
    with pytest.raises(ValueError,match='无法核验'):
        generate_answer(provider,'synthetic','When?',sources,tmp_path/'answer',execute)


def test_semantic_language_scope_is_not_inferred_from_external_model_name(data):
    assert language_support(None)['semantic_scope']=='unconfigured'
    assert language_support({'provider':'builtin'})['semantic_scope']=='zh'
    assert language_support({'model':'sounds-multilingual'})['semantic_scope']=='model_dependent'
    status=data[0].get('/api/retrieval').json()
    assert status['language_support']['keyword_languages']==['zh','en']


def translated_source(data):
    from app.analysis import OpenAICompatibleProvider
    from app.chinese_record import translation_file, write_translation

    client, store, _, pid = data
    mid = uid()
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(mid,'翻译测试','sample.wav'))
    original = {'id':'translated','start':12,'end':16,'speaker':'Alice','text':'We will ship the prototype by Tuesday.'}
    store.save_revision(mid,[
        {'id':'earlier','start':0,'end':3,'speaker':'Bob','text':'开场说明。'}, original,
    ],'manual')
    provider = OpenAICompatibleProvider(store.one('SELECT * FROM llm_configs WHERE id=?',(pid,)))
    path = translation_file(store.directory(mid),original,'en',provider)
    write_translation(path,'我们将在星期二发运样机。')
    return mid, original, path


def test_translation_keyword_matches_one_original_and_rejects_stale_result(data):
    client, store, _, _ = data
    mid, original, path = translated_source(data)
    def search(query, **filters):
        response = client.post('/api/retrieval/search', json={'query':query,'meeting_id':mid,**filters})
        assert response.status_code == 200, response.text
        return response.json()['results']

    hit, = search('发运样机')
    assert search('prototype 发运样机')[0]['id'] == hit['id']
    assert len(search('prototype 发运样机')) == 1
    assert hit['source_segment_ids'] == ['translated'] and hit['start'] == 12
    assert hit['translation_zh'] == '我们将在星期二发运样机。' and hit['source_language'] == 'en'
    assert original['text'] in hit['content']
    token = {key:hit[key] for key in ('id','fingerprint')}
    assert client.post('/api/retrieval/resolve',json=token).status_code == 200

    note = client.post(f'/api/meetings/{mid}/notes',json={'raw_text':'个人样机备注','note_scope':'personal'})
    assert note.status_code == 201, note.text
    assert not search('个人样机备注')
    assert search('个人样机备注',include_personal=True)[0]['note_scope']=='personal'

    path.unlink()
    assert not search('发运样机')
    assert client.post('/api/retrieval/resolve',json=token).status_code == 409
    assert search('prototype')
    from app.chinese_record import write_translation
    write_translation(path,'我们将在星期二发运样机。')
    assert search('发运样机')
    store.save_revision(mid,[{**original,'text':'We will inspect the prototype by Tuesday.'}],'manual')
    assert not search('发运样机')
    assert 'inspect' in search('prototype')[0]['content']


@pytest.mark.parametrize('change,reason', [('translation','translation_changed'),('original','source_changed')])
def test_translated_qa_previews_both_languages_and_keeps_exact_original_citations(data, monkeypatch, change, reason):
    from app.qa import validate_answer

    client, store, app, pid = data
    mid, original, path = translated_source(data)
    response = client.post('/api/retrieval/qa/prepare',json={
        'profile_id':pid,'question':'什么时候发运样机？','query':'发运样机','meeting_id':mid,
    })
    assert response.status_code == 201, response.text
    run = response.json()
    source, = run['sources']
    assert source['translation_zh'] == '我们将在星期二发运样机。'
    assert source['content'] == original['text'] and source['start'] == 12
    def answer(quote):
        return Answer(status='answered',claims=[{'text':'星期二发运。','kind':'record',
            'citations':[{'source':source['label'],'quote':quote}]}])
    with pytest.raises(ValueError, match='无法核验'):
        validate_answer(answer(source['translation_zh']), [source])

    def generate(provider, model, question, sources, *_):
        assert sources == [source]
        return answer('ship the prototype by Tuesday')
    monkeypatch.setattr('app.qa.generate_answer',generate)
    url=f"/api/retrieval/qa/{run['id']}"
    queued=client.post(url+'/generate',json={})
    assert queued.status_code == 201, queued.text
    jid=queued.json()['job']['id']
    store.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,))
    app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    store.execute("UPDATE jobs SET state='completed' WHERE id=?",(jid,))
    assert client.get(url).json()['answer']['claims'][0]['citations'][0]['quote']=='ship the prototype by Tuesday'

    if change=='translation':
        path.unlink()
    else:
        store.save_revision(mid,[{**original,'text':'We will inspect the prototype.'}],'manual')
    stale=client.get(url).json()
    assert stale['stale'] and stale['stale_reasons']==[reason]
    assert stale['answer'] is None and not stale['sources']
