import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import Store
from app.worker import Worker
from app.task_management import classify

def seed(store,jid,state='failed',kind='analysis',error='JSONDecodeError: private transcript secret-key',config=None):
 if not store.one("SELECT 1 FROM meetings WHERE id='meeting'"):store.execute("INSERT INTO meetings(id,title,filename) VALUES('meeting','测试会议','synthetic.wav')")
 store.execute('INSERT INTO jobs(id,meeting_id,state,kind,stage,progress,error,config) VALUES(?,?,?,?,?,?,?,?)',(jid,'meeting',state,kind,'处理失败' if state=='failed' else '等待处理',.6,error,json.dumps(config or {})))

def test_task_diagnostics_are_actionable_and_never_include_raw_logs_or_config(tmp_path):
 app=create_app(tmp_path,start_worker=False);store=app.state.store;seed(store,'failed')
 with TestClient(app) as client:
  response=client.get('/api/runtime/tasks?state=failed');assert response.status_code==200
  item=response.json()['items'][0]
  assert item['failure']['code']=='format' and item['can_retry']
  assert item['meeting_title']=='测试会议'
  assert 'private transcript' not in response.text and 'secret-key' not in response.text
  response=client.get('/api/runtime/diagnostics');assert 'attachment' in response.headers['content-disposition']
  assert 'private transcript' not in response.text and '测试会议' not in response.text and 'secret-key' not in response.text
  assert response.json()['tasks'][0]['error_code']=='format'
  assert client.get('/api/runtime/tasks?state=bad').status_code==400
  assert client.get('/api/runtime/tasks?limit=1000').status_code==400

def test_retry_and_cancel_are_guarded_and_record_sanitized_events(tmp_path):
 app=create_app(tmp_path,start_worker=False);store=app.state.store;seed(store,'job')
 with TestClient(app) as client:
  assert client.post('/api/jobs/job/retry').status_code==200
  assert store.one("SELECT progress FROM jobs WHERE id='job'")['progress']==0
  assert client.post('/api/jobs/job/retry').status_code==409
  assert client.post('/api/jobs/job/cancel').status_code==200
  assert client.post('/api/jobs/job/cancel').status_code==200
  events=store.all("SELECT * FROM job_runtime_events WHERE job_id='job'")
  assert [e['action'] for e in events]==['retry','cancel'] and events[0]['error_code']=='format'
  assert 'secret-key' not in json.dumps(events)
  app.state.worker.busy_jid='job'
  item=client.get('/api/runtime/tasks?state=failed').json()['items'][0]
  assert item['exiting'] and not item['can_retry']
  assert client.post('/api/jobs/job/retry').status_code==409
  app.state.worker.busy_jid=None
  store.execute("DELETE FROM jobs WHERE id='job'")
  assert not store.all('SELECT * FROM job_runtime_events')

def test_stale_model_and_index_configs_require_new_task(tmp_path):
 app=create_app(tmp_path,start_worker=False);store=app.state.store
 seed(store,'model',config={'profile_id':'deleted'})
 seed(store,'index',kind='retrieval_index',config={'identity':'old'})
 seed(store,'unknown',kind='future-unknown')
 with TestClient(app) as client:
  data=client.get('/api/runtime/tasks?state=failed').json()['items']
  assert all(not row['can_retry'] for row in data)
  assert client.post('/api/jobs/model/retry').status_code==409
  assert client.post('/api/jobs/index/retry').status_code==409

def test_index_yields_to_live_recognition_without_blocking_other_jobs(tmp_path):
 store=Store(tmp_path);worker=Worker(store)
 seed(store,'index',state='queued',kind='retrieval_index')
 seed(store,'analysis',state='queued')
 store.execute("INSERT INTO live_sessions(recording_id,source,config) VALUES('live','native','{}')")
 assert worker.next_job()['id']=='analysis'
 assert store.one("SELECT stage FROM jobs WHERE id='index'")['stage']=='实时识别期间暂缓索引'
 store.execute("UPDATE jobs SET state='completed' WHERE id='analysis'")
 assert worker.next_job() is None
 store.execute("UPDATE live_sessions SET status='stopped' WHERE recording_id='live'")
 assert worker.next_job()['id']=='index'

def test_failure_categories_do_not_echo_sensitive_details():
 for message,expected in [('HTTP Error 401 token=secret','auth'),('模型超过处理时限 secret','timeout'),('原文引用不存在 secret','reference'),('connection refused secret','connection'),('模型配置已删除 secret','changed')]:
  value=classify(message);assert value['code']==expected and 'secret' not in str(value)

def test_vector_model_switch_is_blocked_while_live_recognition_runs(tmp_path):
 app=create_app(tmp_path,start_worker=False)
 with TestClient(app) as client:
  app.state.store.execute("INSERT INTO live_sessions(recording_id,source,config) VALUES('active','native','{}')")
  result=client.put('/api/retrieval/config',json={'provider':'builtin'})
  assert result.status_code==409 and '实时识别' in result.json()['detail']
