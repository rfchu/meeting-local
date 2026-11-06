"""Contract observations are ordered reads, never recording commands or durable revisions."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from fastapi.testclient import TestClient
from app.main import create_app
from app.recording_runtime import RecordingRuntime
from tests.test_recording_faults import native_fixture


def test_http_runtime_contract_keeps_legacy_fields_and_browser_ownership(tmp_path):
    with TestClient(create_app(tmp_path,start_worker=False)) as client:
        rid=client.post('/api/recordings',json={}).json()['id']
        response=client.get(f'/api/recordings/{rid}/health').json();value=response['runtime']
        assert value['schema_version']==1 and value['recording_id']==rid
        assert value['identity']=={'recording_id':rid,'live_session_id':None,'meeting_id':None}
        assert value['ownership']=={'capture':'browser_page','storage':'local_service','recognition':'local_service'}
        assert value['source']=='browser' and value['capture']['state']=='recording'
        assert all(k in value for k in ('phase','capture','storage','asr','observed_at'))
        assert value['snapshot']['sequence']>0 and len(value['snapshot']['scope_id'])==32
        assert response['schema_version']==1  # Existing health schema unchanged.


def test_native_contract_reads_are_ordered_without_touching_audio_or_page_lease(tmp_path):
    _,recordings,_,native,runtime,rid,folder=native_fixture(tmp_path)
    process=Mock();process.poll.return_value=None;native.processes[rid]=process;native.last_seen[rid]=123
    audio=(folder/'system.pcm').read_bytes();metadata=(folder/'session.json').read_bytes()
    with ThreadPoolExecutor(max_workers=4) as pool:
        views=list(pool.map(lambda _:runtime.inspect(rid)['runtime'],range(20)))
    assert sorted(v['snapshot']['sequence'] for v in views)==list(range(1,21))
    assert len({v['snapshot']['scope_id'] for v in views})==1
    assert all(v['ownership']['capture']=='native_service' for v in views)
    assert (folder/'system.pcm').read_bytes()==audio and (folder/'session.json').read_bytes()==metadata
    assert native.last_seen[rid]==123
    process.stdin.write.assert_not_called();process.terminate.assert_not_called()


def test_recreated_runtime_has_new_ordering_scope_not_a_persistent_command_revision(tmp_path):
    store,recordings,journal,native,runtime,rid,_,=native_fixture(tmp_path)
    before=runtime.read(rid)['snapshot']
    restored=RecordingRuntime(store,recordings,native,runtime.health,journal)
    after=restored.read(rid)['snapshot']
    assert before['scope_id']!=after['scope_id'] and after['sequence']==1


def test_identity_tracks_existing_live_session_and_meeting_without_inventing_ids(tmp_path):
    store,_,_,_,runtime,rid,_=native_fixture(tmp_path)
    store.execute('INSERT INTO live_sessions(recording_id,source,config) VALUES(?,?,?)',(rid,'native',json.dumps({'engine':'qwen'})))
    assert runtime.read(rid)['identity']=={'recording_id':rid,'live_session_id':rid,'meeting_id':None}
    store.execute('INSERT INTO meetings(id,title,filename) VALUES(?,?,?)',(rid,'synthetic','synthetic.wav'))
    assert runtime.read(rid)['identity']=={'recording_id':rid,'live_session_id':rid,'meeting_id':rid}
