"""Private real-speech workflow tested with synthetic audio only; no real model/device."""
import io,json,sqlite3,subprocess,sys,wave
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.quality_samples import QualitySamples


def audio(seconds=2):
    result=io.BytesIO()
    with wave.open(result,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\x70\x17'*round(seconds*16000))
    return result.getvalue()


def reviewed(samples,text='星河项目周三交付。'):
    row=samples.add(audio(),['terms'],True,True)
    return samples.annotate(row['id'],0,text,True)


def test_import_rights_and_real_source_are_required_before_audio_conversion(tmp_path,monkeypatch):
    samples=QualitySamples(tmp_path)
    monkeypatch.setattr('app.quality_samples.normalize_audio',lambda *_:pytest.fail('not authorized'))
    for rights,real in ((False,True),(True,False),(False,False)):
        with pytest.raises(ValueError,match='权限'):samples.add(b'private', ['clean'],rights,real)
    assert samples.summary()['total']==0


def test_upload_is_draft_and_cannot_be_frozen_as_gold(tmp_path):
    samples=QualitySamples(tmp_path);row=samples.add(audio(),['numbers'],True,True)
    assert row['reference']=='' and row['version']==0 and row['state']=='draft'
    assert samples.summary()['human_reviewed']==0
    with pytest.raises(ValueError):samples.freeze([{'id':row['id'],'version':0}])
    with pytest.raises(ValueError,match='回听'):samples.annotate(row['id'],0,'参考文字',False)
    assert 'reference' not in samples.listing()[0]


def test_reference_versions_are_immutable_and_old_datasets_remain_pinned(tmp_path):
    samples=QualitySamples(tmp_path);one=reviewed(samples)
    ds=samples.freeze([{'id':one['id'],'version':1}]);before=samples.dataset(ds['id'])
    perfect=samples.score(ds['id'],{one['id']:'星河项目周三交付。'},{'generation':'fixture'})
    assert perfect['metrics']['micro_cer']==0
    samples.save_report(ds['id'],perfect)
    samples.annotate(one['id'],1,'星河项目周四交付。',True)
    assert samples.dataset(ds['id'])==before
    assert not samples.datasets()[0]['current_annotations'] and not samples.reports()[0]['current_annotations']
    with pytest.raises(ValueError,match='版本'):samples.annotate(one['id'],1,'迟到参考',True)
    with pytest.raises(ValueError):samples.freeze([{'id':one['id'],'version':1}])
    restored=QualitySamples(tmp_path)
    assert restored.get(one['id'])['version']==2 and restored.dataset(ds['id'])==before


def test_audio_or_dataset_mutation_blocks_scoring_and_reference_acceptance(tmp_path):
    samples=QualitySamples(tmp_path);row=reviewed(samples);ds=samples.freeze([{'id':row['id'],'version':1}])
    path=samples.audio_path(row['id']);path.write_bytes(path.read_bytes()+b'changed')
    for fn in (lambda:samples.dataset(ds['id']),lambda:samples.verified_audio(row['id']),lambda:samples.annotate(row['id'],1,'新的文字',True)):
        with pytest.raises(ValueError,match='音频'):fn()


def test_predictions_must_cover_all_frozen_samples_and_metadata_is_redacted(tmp_path):
    samples=QualitySamples(tmp_path);row=reviewed(samples);ds=samples.freeze([{'id':row['id'],'version':1}])
    with pytest.raises(ValueError,match='完整'):samples.score(ds['id'],{}, {})
    report=samples.score(ds['id'],{row['id']:'星河项目周三交付。'},{'engine':'whisper','generation':'fixture','token':'private-token'})
    samples.save_report(ds['id'],report)
    assert 'private-token' not in json.dumps(samples.reports())
    assert '星河' not in json.dumps(samples.summary()) and '星河' not in json.dumps(samples.reports())
    assert report['mode']=='private_real_speech_asr' and report['reference_origin']=='user_attested_human_audio_review'


def test_bad_or_long_audio_is_rejected_without_publishing_sample(tmp_path):
    samples=QualitySamples(tmp_path)
    for payload in (b'not audio',audio(.2),audio(121)):
        with pytest.raises(ValueError):samples.add(payload,['clean'],True,True)
    assert samples.summary()['total']==0
    assert not list(samples.root.glob('*.wav'))


def test_source_provenance_is_fixed_before_first_annotation(tmp_path):
    samples=QualitySamples(tmp_path);row=samples.add(audio(),['terms'],True,True)
    source={'kind':'selected_meeting','meeting_id':'a'*32,'revision':2,'segment_ids':['s1'],'start':0,'end':2}
    samples.record_source(row['id'],source)
    assert samples.get(row['id'])['source']==source
    samples.annotate(row['id'],0,'这是参考。',True)
    with pytest.raises(ValueError):samples.record_source(row['id'],source)


def test_http_upload_review_freeze_and_audio_stay_local_and_passive(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        base='/api/runtime/quality-samples'
        assert client.get(base).json()['summary']['total']==0
        file={'file':('do-not-retain-name.wav',audio(),'audio/wav')}
        assert client.post(base,files=file).status_code==409
        response=client.post(base,files=file,data={'rights_confirmed':'true','real_speech':'true','tags':'terms'})
        assert response.status_code==201;sid=response.json()['id']
        assert 'do-not-retain-name' not in response.text
        assert client.get(base+f'/{sid}/audio').headers['cache-control']=='no-store'
        assert client.put(base+f'/{sid}/annotation',json={'expected_version':0,'reference':'参考文字。','audio_reviewed':True}).status_code==200
        assert client.put(base+f'/{sid}/annotation',json={'expected_version':0,'reference':'迟到文字。','audio_reviewed':True}).status_code==409
        assert client.post('/api/runtime/quality-datasets',json={'members':[{'id':sid,'version':1}]}).status_code==201
        assert '参考文字' not in client.get(base).text
        assert client.get('/api/native-recordings').json()==[]


def test_unavailable_private_store_does_not_disable_meeting_service(tmp_path):
    (tmp_path/'quality-samples').write_text('blocked')
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        assert client.get('/api/runtime/quality-samples').status_code==503
        assert client.get('/api/runtime/lifecycle').status_code==200


def test_private_cli_cannot_start_inference_without_explicit_flag(tmp_path):
    result=subprocess.run([sys.executable,'scripts/evaluate_private_quality.py','--data-root',str(tmp_path),'--dataset-id','a'*32,'--engine','qwen'],capture_output=True,text=True)
    assert result.returncode==2 and '--run-local-models' in result.stderr
    assert not (tmp_path/'quality-samples').exists()


def test_private_report_rubric_status_is_separate_from_annotation_version(tmp_path,monkeypatch):
    samples=QualitySamples(tmp_path);row=reviewed(samples);ds=samples.freeze([{'id':row['id'],'version':1}])
    report=samples.score(ds['id'],{row['id']:'星河项目周三交付。'}, {})
    samples.save_report(ds['id'],report)
    assert samples.reports()[0]['current_rubric']
    monkeypatch.setattr(samples,'rubric',lambda:'changed')
    assert samples.reports()[0]['current_annotations'] and not samples.reports()[0]['current_rubric']


def test_review_of_existing_transcript_is_recorded_separately_from_blind_transcription(tmp_path):
    samples=QualitySamples(tmp_path);row=samples.add(audio(),['terms'],True,True)
    saved=samples.annotate(row['id'],0,'已回听校对的文字。',True,'user_verified_existing_transcript')
    assert saved['reference_origin']=='user_verified_existing_transcript'
    ds=samples.freeze([{'id':row['id'],'version':1}])
    report=samples.score(ds['id'],{row['id']:'已回听校对的文字。'}, {})
    assert report['annotation_methods']==['user_verified_existing_transcript']


def test_legacy_snapshot_without_annotation_method_remains_readable_without_promoting_provenance(tmp_path):
    from app.quality_evaluation import digest
    samples=QualitySamples(tmp_path);row=reviewed(samples);ds=samples.freeze([{'id':row['id'],'version':1}])
    members=samples.dataset(ds['id'])['members'];members[0].pop('annotation_origin')
    fingerprint=digest({'scope':'private_user_attested_real_speech','members':members})
    with samples.connect() as db:db.execute('UPDATE datasets SET members=?,fingerprint=? WHERE id=?',(json.dumps(members),fingerprint,ds['id']))
    report=samples.score(ds['id'],{row['id']:'星河项目周三交付。'}, {})
    assert report['annotation_methods']==['legacy_human_audio_review'] and report['metrics']['micro_cer']==0
