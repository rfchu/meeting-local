"""Frozen synthetic evaluation invariants; no actual models, devices or network."""
import copy,json,subprocess,sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app import quality_evaluation as q


def gold():return {r['id']:r['reference'] for r in q.load_suite()[0]['cases'] if r['track']=='asr_text'}
def fingerprint(report):report['report_fingerprint']=q.digest({k:v for k,v in report.items() if k!='report_fingerprint'});return report


def test_fixed_corpus_and_current_protocol_pipeline_are_complete():
    corpus,_,_=q.load_suite();assert len(corpus['cases'])==20
    report=q.offline();q.check_report(report)
    assert report['mode']=='offline_protocol' and report['metrics']=={'probe_count':23,'passed_probes':23,'probe_pass_rate':1.0,'model_calls':0}
    assert report['subject']['generation']=='fixed_candidates_not_model_outputs'
    assert q.compare(report,report)['regression_gate_passed']


def test_cer_normalization_and_micro_average_keep_digits_and_insertions():
    assert q.normalize(' ＡＢＣ，中文。\n')=='abc中文'
    assert q.normalize('五万元')!=q.normalize('5万元')
    assert q.distance('会议','会议会议会议')==4
    predictions=gold();report=q.score_asr(predictions,{'generation':'fixture'})
    assert report['metrics']['micro_cer']==0
    assert all('hypothesis' not in row for row in report['rows'])
    predictions[next(iter(predictions))]=''
    changed=q.score_asr(predictions,{'generation':'fixture'})
    expected=next(row['reference_characters'] for row in changed['rows'] if row['hypothesis_characters']==0)
    assert changed['metrics']['micro_cer']==expected/changed['metrics']['reference_characters']
    assert not q.compare(report,changed)['regression_gate_passed']


@pytest.mark.parametrize('mutation',['missing','extra','wrong_type'])
def test_candidate_cannot_skip_failed_samples(mutation):
    data=gold()
    if mutation=='missing':data.pop(next(iter(data)))
    elif mutation=='extra':data['invented']='extra'
    else:data[next(iter(data))]=[]
    with pytest.raises(ValueError):q.score_asr(data,{})


def test_suite_audio_and_manifest_cannot_silently_drift(tmp_path):
    import shutil
    shutil.copytree(q.FIXTURES,tmp_path/'fixtures');folder=tmp_path/'fixtures'
    audio=next((folder/'audio').glob('*.wav'));audio.write_bytes(audio.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='指纹'):q.load_suite(folder)
    shutil.copy2(q.FIXTURES/'audio'/audio.name,audio)
    data=q.read_json(folder/'corpus-v1.json');data['cases'][0]['id']=data['cases'][1]['id'];(folder/'corpus-v1.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='重复'):q.load_suite(folder)


def test_report_comparison_rejects_changed_rubric_or_corpus_and_tampered_counts():
    baseline=q.score_asr(gold(),{})
    for key in ('suite_fingerprint','rubric_fingerprint','mode'):
        changed=copy.deepcopy(baseline);changed[key]='different';fingerprint(changed)
        with pytest.raises(ValueError):q.compare(baseline,changed)
    changed=copy.deepcopy(baseline);changed['metrics']['micro_cer']=.5;fingerprint(changed)
    with pytest.raises(ValueError,match='汇总'):q.check_report(changed)
    changed=copy.deepcopy(baseline);changed['rows'].append(changed['rows'][0]);fingerprint(changed)
    with pytest.raises(ValueError,match='重复'):q.check_report(changed)
    changed=copy.deepcopy(baseline);changed['metrics']['micro_cer']=.9
    with pytest.raises(ValueError,match='指纹'):q.check_report(changed)


def test_per_probe_regression_is_not_hidden_by_equal_pass_rate():
    baseline=q.offline();candidate=copy.deepcopy(baseline)
    candidate['rows'][0]['passed']=False;candidate['metrics']['passed_probes']-=1;candidate['metrics']['probe_pass_rate']=candidate['metrics']['passed_probes']/candidate['metrics']['probe_count'];fingerprint(candidate)
    result=q.compare(baseline,candidate)
    assert not result['regression_gate_passed'] and result['regressions']==[baseline['rows'][0]['id']]


def test_model_calls_are_explicit_and_outputs_never_overwrite(tmp_path):
    output=tmp_path/'not-created.json'
    result=subprocess.run([sys.executable,'scripts/evaluate_quality.py','asr','--engine','qwen','--output',str(output)],capture_output=True,text=True)
    assert result.returncode==2 and '--run-local-models' in result.stderr and not output.exists()
    output.write_text('keep')
    result=subprocess.run([sys.executable,'scripts/evaluate_quality.py','offline','--output',str(output)],capture_output=True,text=True)
    assert result.returncode==2 and output.read_text()=='keep'


def test_baseline_inventory_does_not_expose_text_or_extra_subject_fields(tmp_path,monkeypatch):
    (tmp_path/'evaluation/baselines').mkdir(parents=True)
    report=q.score_asr(gold(),{'generation':'fixture','token':'private-secret'})
    (tmp_path/'evaluation/baselines/test.json').write_text(json.dumps(report))
    monkeypatch.setattr(q,'ROOT',tmp_path)
    value=q.baseline_inventory();text=json.dumps(value)
    assert value['passive'] and not value['models_started'] and 'private-secret' not in text
    assert 'hypothesis' not in text and 'outputs' not in text


def test_passive_http_baseline_does_not_invoke_scoring_or_models(tmp_path,monkeypatch):
    monkeypatch.setattr(q,'offline',lambda *a:pytest.fail('passive view must not run evaluation'))
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        response=client.get('/api/runtime/quality-baseline')
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['fixed_cases']==20 and not response.json()['models_started']
        assert client.get('/api/native-recordings').json()==[]


def test_cloud_placeholder_preflight_never_reads_config_or_starts_model(tmp_path,monkeypatch):
    from types import SimpleNamespace
    class Provider:
        def identity(self):return [{'path':str(tmp_path/'model.safetensors')}]
        def ready(self):pytest.fail('must not read config or hydrate a cloud placeholder')
    original=Path.stat
    monkeypatch.setattr(Path,'stat',lambda self,*a,**kw:SimpleNamespace(st_flags=0x40000000) if self.name=='model.safetensors' else original(self,*a,**kw))
    assert q.model_input_status(Provider())=='cloud_placeholder'


def test_candidate_hypothesis_text_is_only_retained_when_explicitly_requested():
    text=gold();private=q.score_asr(text,{},include_text=False);synthetic=q.score_asr(text,{},include_text=True)
    assert all('hypothesis' not in row for row in private['rows'])
    assert [row['hypothesis'] for row in synthetic['rows']]==list(text.values())


def test_provider_catalog_does_not_report_cloud_placeholder_ready(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.providers import WhisperCpp,locally_resident
    binary=tmp_path/'whisper';binary.touch();model=tmp_path/'model.bin';model.touch()
    monkeypatch.setenv('WHISPER_CLI',str(binary));monkeypatch.setenv('WHISPER_MODEL',str(model))
    original=Path.stat
    def stat(path,*a,**kw):
        info=original(path,*a,**kw)
        return SimpleNamespace(st_flags=0x40000000,st_mode=info.st_mode) if path==model else info
    monkeypatch.setattr(Path,'stat',stat)
    assert not locally_resident(model) and not WhisperCpp().ready()
