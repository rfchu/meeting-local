from fastapi.testclient import TestClient
from app.main import create_app
from app.db import uid

def setup(tmp_path):
 app=create_app(tmp_path,start_worker=False);store=app.state.store
 ids=[uid(),uid(),uid()]
 for pid,name,kind,default in [(ids[0],'本地A','local',0),(ids[1],'本地B','local',0),(ids[2],'云端','cloud',1)]:
  store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key,is_default) VALUES(?,?,?,?,?,?,?)',(pid,name,kind,'http://127.0.0.1:1234/v1' if kind=='local' else 'https://example.com/v1','test','private-key',default))
 return app,store,ids

def test_effective_choice_priority_and_local_only_fallback(tmp_path):
 app,store,(a,b,cloud)=setup(tmp_path)
 with TestClient(app) as c:
  data=c.get('/api/model-preferences').json();assert data['roles']['overview']['effective_value']==a
  assert 'private-key' not in str(data)
  c.put('/api/model-preferences/overview/default',json={'value':b,'version':0}).raise_for_status()
  c.put('/api/model-preferences/overview/last',json={'value':a}).raise_for_status()
  value=c.get('/api/model-preferences').json()['roles']['overview']
  assert value['effective_value']==a and value['source']=='last'
  value=c.put('/api/model-preferences/overview/last',json={'value':'','version':value['version']}).json()['roles']['overview']
  assert value['effective_value']==b and value['source']=='default'
  store.execute('DELETE FROM llm_configs WHERE provider_type=\'local\'')
  value=c.get('/api/model-preferences').json()['roles']['overview']
  assert value['effective_value']=='' and value['invalid_default']
  value=c.put('/api/model-preferences/overview/default',json={'value':cloud}).json()['roles']['overview']
  assert value['effective_value']==cloud and value['source']=='default'

def test_browser_import_never_overwrites_valid_server_choice_and_ignores_invalid_keys(tmp_path):
 app,store,(a,b,cloud)=setup(tmp_path)
 with TestClient(app) as c:
  c.put('/api/model-preferences/minutes/last',json={'value':a}).raise_for_status()
  data=c.post('/api/model-preferences/import',json={'choices':{'minutes':b,'analysis':b,'live_transcription':'qwen','bad':'anything','qa':'deleted'}}).json()
  assert data['roles']['minutes']['last_value']==a
  assert data['roles']['analysis']['last_value']==b
  assert data['roles']['live_transcription']['effective_value']=='qwen'
  assert 'minutes' not in data['imported'] and 'qa' not in data['imported']
  assert c.put('/api/model-preferences/transcription/default',json={'value':a}).status_code==409
  assert c.put('/api/model-preferences/no-role/last',json={'value':a}).status_code==404

def test_optimistic_versions_and_default_changes_do_not_mutate_tasks(tmp_path):
 app,store,(a,b,_)=setup(tmp_path)
 store.execute("INSERT INTO jobs(id,state,config) VALUES('queued','queued',?)",('{"profile_id":"'+a+'","model":"frozen"}',))
 with TestClient(app) as c:
  c.put('/api/model-preferences/analysis/default',json={'value':a,'version':0}).raise_for_status()
  assert c.put('/api/model-preferences/analysis/default',json={'value':b,'version':0}).status_code==409
  assert store.one("SELECT config FROM jobs WHERE id='queued'")['config']=='{"profile_id":"'+a+'","model":"frozen"}'

def test_explicit_clear_survives_later_browser_import_and_backend_resolver_uses_role(tmp_path):
 from app.model_preferences import resolve_profile
 app,store,(a,b,cloud)=setup(tmp_path)
 with TestClient(app) as c:
  c.put('/api/model-preferences/analysis/default',json={'value':b}).raise_for_status()
  c.put('/api/model-preferences/analysis/last',json={'value':''}).raise_for_status()
  data=c.post('/api/model-preferences/import',json={'choices':{'analysis':a}}).json()
  assert data['roles']['analysis']['last_value']==''
  assert resolve_profile(store,'analysis')['id']==b
  assert resolve_profile(store,'analysis',a)['id']==a
  store.execute('DELETE FROM llm_configs WHERE provider_type=\'local\'')
  import pytest
  from fastapi import HTTPException
  with pytest.raises(HTTPException):resolve_profile(store,'analysis')

def test_inflight_task_locks_profile_parameters_but_allows_metadata_changes(tmp_path):
 app,store,(a,b,_)=setup(tmp_path)
 store.execute("INSERT INTO jobs(id,state,kind,config) VALUES('busy','queued','minutes',?)",('{"profile_id":"'+a+'"}',))
 with TestClient(app) as c:
  body={'name':'本地A改名','provider_type':'local','base_url':'http://127.0.0.1:1234/v1','model':'changed'}
  assert c.put('/api/llm-configs/'+a,json=body).status_code==409
  assert c.delete('/api/llm-configs/'+a).status_code==409
  body['model']='test'
  assert c.put('/api/llm-configs/'+a,json=body).status_code==200
  c.put('/api/model-preferences/minutes/default',json={'value':b}).raise_for_status()
  assert store.one("SELECT config FROM jobs WHERE id='busy'")['config']=='{"profile_id":"'+a+'"}'
