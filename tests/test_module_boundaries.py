"""Composition and external HTTP compatibility after the B3 modular split."""
import hashlib,json
from pathlib import Path
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from app.main import create_app


def test_http_contract_matches_pre_split_v22(tmp_path):
    app=create_app(tmp_path/'contract',start_worker=False)
    schema=app.openapi()
    # Language choices are additive. Keep checking the historical contract without them.
    for definition in schema['components']['schemas'].values():
        for name in ('source_language','output_language'):
            definition.get('properties',{}).pop(name,None)

    assert schema['paths'].pop('/api/recordings/{rid}/chinese-record/{aid}/{index}')
    assert set(schema['paths'].pop('/api/recordings/{rid}/live-audio/{aid}/{index}'))=={'get'}
    assert 'source_track' in schema['components']['schemas']['Segment']['properties']
    schema['components']['schemas']['Segment']['properties'].pop('source_track')
    assert schema['components']['schemas'].pop('TranslationInput')
    assert schema['components']['schemas'].pop('MeetingTranslationInput')
    assert schema['paths'].pop('/api/meetings/{mid}/experimental-drafts/{aid}/chinese-record')
    assert schema['paths'].pop('/api/meetings/{mid}/experimental-drafts/{aid}/chinese-record/{segment_id}')
    assert schema['paths'].pop('/api/meetings/{mid}/chinese-record')
    assert schema['paths'].pop('/api/meetings/{mid}/chinese-record/{segment_id}')
    assert schema['paths'].pop('/api/runtime/workspace')
    assert schema['paths'].pop('/api/meeting-library')
    assert schema['components']['schemas'].pop('LibraryUpdate')
    assert schema['components']['schemas']['MinutesSaveInput']['properties'].pop('merge_from_minutes_id')
    for path in ('/api/runtime/quality-samples','/api/runtime/quality-samples/{sid}','/api/runtime/quality-samples/{sid}/audio','/api/runtime/quality-samples/{sid}/annotation','/api/runtime/quality-datasets'):assert schema['paths'].pop(path)
    for name in ('QualityAnnotationInput','QualityMember','QualityFreezeInput','Body_add_api_runtime_quality_samples_post'):assert schema['components']['schemas'].pop(name)
    assert schema['paths'].pop('/api/runtime/quality-baseline')
    assert schema['paths'].pop('/api/runtime/release-readiness')
    assert schema['paths'].pop('/api/runtime/recording-checks/{cid}/acceptance')
    assert schema['paths'].pop('/api/runtime/recording-checks/{cid}/acceptance-review')
    assert schema['components']['schemas'].pop('AcceptanceReview')
    assert schema['paths'].pop('/api/runtime/capture-control')
    assert schema['paths'].pop('/api/recordings/{rid}/live-session/recover')
    assert schema['components']['schemas'].pop('RecoverLiveSession')
    assert schema['paths'].pop('/api/native-capture/{rid}/recover-control')
    assert schema['components']['schemas'].pop('RecoverCaptureControl')
    assert schema['paths'].pop('/api/runtime/host')
    assert schema['paths'].pop('/api/runtime/host-diagnostics')
    assert schema['paths'].pop('/api/meetings/{mid}/minutes/delivery-readiness')
    assert schema['paths'].pop('/api/meetings/{mid}/minutes/delivery-finalize')
    assert schema['components']['schemas'].pop('DeliveryFinalizeInput')
    for route,method in (('audience-preview','post'),('audience-finalize','post'),('deliveries/{delivery_id}/sent','put')):
        assert set(schema['paths'].pop('/api/meetings/{mid}/minutes/'+route))=={method}
    for name in ('AudiencePreviewInput','AudienceFinalizeInput'):
        assert schema['components']['schemas'][name]['properties']['template_version']['default']==0
    for name in ('AudiencePreviewInput','AudienceFinalizeInput','DeliverySentInput'):
        assert schema['components']['schemas'].pop(name)
    assert schema['paths'].pop('/api/meetings/{mid}/handoff/sources')
    assert schema['paths'].pop('/api/meetings/{mid}/handoff/source-reviews')
    assert schema['components']['schemas'].pop('SourceReviewInput')
    assert schema['paths'].pop('/api/runtime/resources')
    assert schema['paths'].pop('/api/meetings/{mid}/minutes/jobs')
    assert schema['components']['schemas'].pop('MinutesTaskInput')
    assert set(schema['paths'].pop('/api/recordings/{rid}/notes/shorthand'))=={'get','post'}
    assert schema['components']['schemas'].pop('ShorthandInput')
    assert schema['paths'].pop('/api/recordings/{rid}/candidates')
    assert schema['paths'].pop('/api/recordings/{rid}/candidates/{cid}/accept')
    assert schema['paths'].pop('/api/recordings/{rid}/candidates/{cid}/reject')
    schema['paths']['/api/runtime/tasks']['get']['parameters']=[parameter for parameter in schema['paths']['/api/runtime/tasks']['get']['parameters'] if parameter['name'] not in ('meeting_id','job_id')]
    assert schema['components']['schemas']['SearchInput']['properties'].pop('include_personal')['default'] is False
    assert set(schema['paths'].pop('/api/audience-templates/{audience}'))=={'get','put'}
    assert schema['components']['schemas'].pop('AudienceTemplateUpdate')
    assert set(schema['paths'].pop('/api/runtime/companion'))=={'get'}
    assert set(schema['paths'].pop('/api/runtime/companion/translation'))=={'post'}
    assert schema['components']['schemas']['InterviewPreparation']['properties'].pop('speaker_roles')
    assert schema['components']['schemas']['InterviewQA']['properties'].pop('question_evidence')
    assert schema['components']['schemas'].pop('InterviewSpeakerRoles')
    assert schema['components']['schemas']['ComposeInput']['properties'].pop('expected_analysis_id')
    payload=json.dumps(schema,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    # Captured from the running v22 service before splitting: 116 paths, 135 operations.
    assert hashlib.sha256(payload.encode()).hexdigest()=='fac4d2387540dd6ea2f8f29205390b870e6d7a8695f1f743831ce1f2ec026b90'
    operations=[(method,route.path) for route in app.routes if isinstance(route,APIRoute) for method in route.methods]
    assert len(operations)==len(set(operations))==175


def test_two_applications_do_not_share_recording_or_meeting_resources(tmp_path):
    left=create_app(tmp_path/'left',start_worker=False);right=create_app(tmp_path/'right',start_worker=False)
    for name in ('store','worker','recordings','live_sessions','live_asr_lock','recording_commands','resident','meetings','configurations'):
        assert getattr(left.state.services,name) is not getattr(right.state.services,name)
    assert left.state.worker is left.state.services.worker
    assert left.state.live_sessions is left.state.services.live_sessions
    mid='a'*32
    left.state.store.execute("INSERT INTO meetings(id,title,filename) VALUES(?,?,?)",(mid,'Synthetic isolation','fixture.wav'))
    with TestClient(left) as a,TestClient(right) as b:
        assert a.get('/api/meetings/'+mid).status_code==200
        assert b.get('/api/meetings/'+mid).status_code==404
        recording=a.post('/api/recordings',json={'mime_type':'audio/webm'}).json()['id']
        assert b.get('/api/recordings/'+recording+'/health').status_code==404


@pytest.mark.parametrize('path',['/api/runtime/overview','/api/retrieval','/api/recordings','/api/meetings','/api/minutes-templates'])
def test_local_only_guard_covers_every_feature_router(tmp_path,path):
    with TestClient(create_app(tmp_path/'security',start_worker=False)) as client:
        assert client.get(path,headers={'host':'remote.example'}).status_code==403
        assert client.get(path,headers={'origin':'https://remote.example'}).status_code==403


def test_explicit_external_workspace_paths_are_shared_by_app_and_maintenance(tmp_path,monkeypatch):
    import os,subprocess,sys
    from app.paths import RuntimePaths
    source=tmp_path/'local-data';destination=tmp_path/'local-backups'
    monkeypatch.setenv('MEETING_DATA',str(source));monkeypatch.setenv('MEETING_BACKUPS',str(destination))
    paths=RuntimePaths.from_env()
    assert paths.data==source and paths.backups==destination
    assert not source.exists() and not destination.exists()
    app=create_app(start_worker=False)
    assert app.state.store.root==app.state.paths.data==source
    run=subprocess.run([sys.executable,'-m','scripts.manage_backups','create'],capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stderr
    result=json.loads(run.stdout)
    assert Path(result['backup_directory']).parent==destination and result['status']=='verified'
    assert create_app(tmp_path/'override',start_worker=False).state.store.root!=source


def test_restored_external_directory_can_start_without_switching_original(tmp_path):
    from app.backups import create_backup,restore_backup
    original=create_app(tmp_path/'original',start_worker=False)
    original.state.store.execute("INSERT INTO meetings(id,title,filename) VALUES(?,?,?)",('b'*32,'Synthetic restored workspace','fixture.wav'))
    bundle=tmp_path/'backup';create_backup(original.state.store.root,bundle)
    restored=tmp_path/'external'/'restored';restore_backup(bundle,restored)
    with TestClient(create_app(restored,start_worker=False)) as client:
        assert client.get('/api/meetings').json()[0]['id']=='b'*32
    assert original.state.store.one('SELECT title FROM meetings')['title']=='Synthetic restored workspace'
