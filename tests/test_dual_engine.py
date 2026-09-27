import io,json,wave
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.providers import QwenMLX,get_asr_provider
from app.alignment import aligned_segments
from app.db import uid,encode

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as c:yield c

def create_meeting(c):
    stream=io.BytesIO()
    with wave.open(stream,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*16000)
    return c.post('/api/meetings',files={'file':('双引擎.wav',stream.getvalue(),'audio/wav')}).json()['id']

def test_engine_validation_and_no_silent_fallback(client,monkeypatch):
    monkeypatch.setattr('app.providers.QwenMLX.ready',lambda _:False)
    mid=create_meeting(client)
    assert client.post(f'/api/meetings/{mid}/jobs',json={'engine':'cloud'}).status_code==422
    r=client.post(f'/api/meetings/{mid}/jobs',json={'engine':'qwen'})
    assert r.status_code==409 and '千问' in r.json()['detail']
    assert client.app.state.store.all('SELECT * FROM jobs')==[]
    catalog=client.get('/api/status').json()['engines']
    assert {e['id'] for e in catalog}=={'qwen','whisper'}

def test_selected_engine_persists_on_retry(client,monkeypatch):
    monkeypatch.setattr('app.providers.QwenMLX.ready',lambda _:True)
    mid=create_meeting(client)
    r=client.post(f'/api/meetings/{mid}/jobs',json={'engine':'qwen'});assert r.status_code==201
    jid=r.json()['id']
    client.post(f'/api/jobs/{jid}/cancel').raise_for_status()
    client.post(f'/api/jobs/{jid}/retry').raise_for_status()
    row=client.app.state.store.one('SELECT * FROM jobs WHERE id=?',(jid,));config=json.loads(row['config'])
    assert config['engine']=='qwen' and config['chunk_seconds']==120 and not config['punctuation']

def test_old_requests_still_default_to_whisper(client,monkeypatch):
    monkeypatch.setattr('app.providers.WhisperCpp.ready',lambda _:True)
    mid=create_meeting(client)
    client.post(f'/api/meetings/{mid}/jobs',json={}).raise_for_status()
    config=json.loads(client.app.state.store.one('SELECT config FROM jobs')['config'])
    assert config['engine']=='whisper' and config['chunk_seconds']==300

def test_actual_worker_routes_qwen_and_records_provenance(client,monkeypatch):
    calls=[]
    class FakeQwen:
        engine_id='qwen';label='千问测试';model='qwen-test';aligner='aligner-test';chunk_seconds=120;needs_punctuation=False
        def ready(self):return True
        def identity(self):return []
        def transcribe(self,audio,output,run):
            calls.append(audio)
            return [{'start':0.1,'end':.9,'text':'千问结果。','speaker':'未分配'}]
    monkeypatch.setattr('app.worker.get_asr_provider',lambda engine:FakeQwen() if engine=='qwen' else pytest.fail('wrong engine'))
    mid=create_meeting(client);store=client.app.state.store;jid=uid()
    config={'engine':'qwen','base_revision':0,'pipeline_version':2,'diarization':False}
    store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'running','test',encode(config)))
    client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert len(calls)==1
    m=client.get(f'/api/meetings/{mid}').json()
    assert m['segments'][0]['text']=='千问结果。'
    meta=json.loads(m['revisions'][0]['source'])
    assert meta['engine']=='qwen' and meta['aligner']=='aligner-test'
    assert m['jobs'][0]['state']=='completed'

def test_changed_qwen_model_rejects_old_checkpoints(client,monkeypatch):
    mid=create_meeting(client);store=client.app.state.store;jid=uid()
    config={'engine':'qwen','base_revision':0,'pipeline_version':2,'engine_identity':[{'old':'model'}]}
    store.execute('INSERT INTO jobs(id,meeting_id,state,stage,config) VALUES(?,?,?,?,?)',(jid,mid,'running','test',encode(config)))
    with pytest.raises(RuntimeError,match='变更'):client.app.state.worker.process(store.one('SELECT * FROM jobs WHERE id=?',(jid,)))
    assert store.one('SELECT revision FROM meetings WHERE id=?',(mid,))['revision']==0

def test_model_readiness_requires_all_shards(tmp_path):
    for name in ('config.json','tokenizer.json','preprocessor_config.json'):(tmp_path/name).write_text('{}')
    (tmp_path/'model.safetensors.index.json').write_text(json.dumps({'weight_map':{'a':'a.safetensors','b':'b.safetensors'}}))
    (tmp_path/'a.safetensors').write_bytes(b'weights')
    assert not QwenMLX.model_ready(tmp_path)
    (tmp_path/'b.safetensors').write_bytes(b'weights')
    assert QwenMLX.model_ready(tmp_path)

def test_alignment_preserves_chinese_english_and_punctuation():
    text='项目 OpenAI，周五交付。明天确认！'
    tokens=['项','目','OpenAI','周','五','交','付','明','天','确','认']
    items=[{'text':t,'start':i*.3,'end':i*.3+.2} for i,t in enumerate(tokens)]
    segments=aligned_segments(text,items,4)
    assert ''.join(s['text'] for s in segments)==text
    assert len(segments)==2 and segments[0]['start']==0
    assert segments[1]['start']==pytest.approx(2.1)

@pytest.mark.parametrize('items',[
    [],[{'text':'别','start':0,'end':1}],
    [{'text':'测','start':0,'end':-1}],
    [{'text':'测','start':float('nan'),'end':1}],
    [{'text':'测','start':0,'end':20}],
])
def test_alignment_rejects_unreliable_times_or_missing_text(items):
    with pytest.raises(ValueError):aligned_segments('测。',items,2)

def test_alignment_pause_splits_without_dropping_words():
    result=aligned_segments('可以继续',[{'text':'可','start':0,'end':.2},{'text':'以','start':.2,'end':.4},
      {'text':'继','start':2,'end':2.2},{'text':'续','start':2.2,'end':2.4}],3)
    assert [s['text'] for s in result]==['可以','继续']


def test_model_readiness_supports_official_split_tokenizer(tmp_path):
    for name in ('config.json','preprocessor_config.json','tokenizer_config.json','vocab.json','merges.txt'):
        (tmp_path/name).write_text('{}')
    (tmp_path/'model.safetensors').write_bytes(b'weights')
    assert QwenMLX.model_ready(tmp_path)
    (tmp_path/'merges.txt').unlink()
    assert not QwenMLX.model_ready(tmp_path)


def test_qwen_capped_output_is_retried_as_smaller_audio():
    import numpy as np
    from scripts.qwen_transcribe import transcribe_adaptive
    class Result:
        def __init__(self,text,tokens):self.text=text;self.generation_tokens=tokens
    class Model:
        def __init__(self):self.lengths=[]
        def generate(self,audio,**_):
            self.lengths.append(len(audio))
            return Result('达到上限' if len(audio)>800 else '成功',4096 if len(audio)>800 else 8)
    model=Model()
    text,attempts=transcribe_adaptive(model,np.zeros(1600,dtype=np.float32),16)
    assert text=='成功\n成功'
    assert model.lengths[0]==1600 and max(model.lengths[1:])<1600
    assert attempts[0]['capped'] and not attempts[-1]['capped']
