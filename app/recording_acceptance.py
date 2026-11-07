"""Evidence-bound, human-reviewed hardware acceptance; observation is never certification."""
import hashlib,json,platform,sqlite3
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field

CASES={
    'short_capture':('原生录音短测',60,None),
    'long_capture':('三小时连续录音',10800,None),
    'wake_return':('睡眠唤醒后恢复',0,'wake_return'),
    'other_app_changed':('其他音频软件切换',0,'other_app_changed'),
    'asr_recovery':('识别恢复后新文字确认',0,None),
    'clean_stop':('明确停止与音轨保留',0,None),
}
Case=Literal['short_capture','long_capture','wake_return','other_app_changed','asr_recovery','clean_stop']


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def environment():
    root=Path(__file__).resolve().parents[1];digest=hashlib.sha256()
    for path in sorted([*root.glob('app/**/*.py'),*root.glob('native/AudioCapture/*.swift')]):
        digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    from .native_capture import HELPER
    try:helper_digest=hashlib.sha256(HELPER.read_bytes()).hexdigest()
    except OSError:helper_digest='unavailable'
    return fingerprint({'runtime':digest.hexdigest(),'helper':helper_digest,'os':platform.mac_ver()[0],'architecture':platform.machine()})


def evaluate(report,case,current_environment=None):
    label,minimum,marker=CASES[case];reasons=[];samples=report.get('samples',[]);markers=report.get('markers',[])
    if current_environment and report.get('capture_environment')!=current_environment:reasons.append('观察时的运行版本缺失或与当前不同，不能补认当前版本验收')
    if report.get('state')=='observing':reasons.append('请先结束观察，再回听核对')
    if report.get('state')=='interrupted':reasons.append('观察中断，不能据此确认完整过程')
    if report.get('samples_truncated'):reasons.append('采样历史不完整')
    if report.get('elapsed_seconds',0)<minimum:reasons.append('观察时长不足')
    if minimum:
        if len(samples)<max(2,minimum//5*0.8):reasons.append('连续采样数量不足')
        if report.get('max_observation_gap_seconds',0)>20:reasons.append('连续观察存在较长缺口')
    flags=report.get('flags',{})
    if any(flags.get(k) for k in ('audio_file_shrank','storage_unconfirmed','status_unavailable','capture_unknown','capture_ended','capture_not_running')):reasons.append('存在采集或文件异常，需重新验证')
    if marker:
        selected=[m for m in markers if m.get('kind')==marker]
        if not selected:reasons.append('缺少现场步骤标记')
        elif not any(any(s.get('elapsed_seconds',0)<m.get('elapsed',0) for s in samples) and any(s.get('elapsed_seconds',0)>m.get('elapsed',0) and s.get('file_growth_bytes',0)>0 for s in samples) for m in selected):reasons.append('现场步骤前后的文件增长证据不足')
    if case in ('short_capture','long_capture'):
        if not samples or (samples[-1].get('saved_bytes') or 0)<=(samples[0].get('saved_bytes') or 0):reasons.append('缺少音轨持续增长证据')
    if case=='clean_stop' and not (report.get('end_reason')=='recording_stopped' and samples and samples[-1].get('stop_confirmed') is True and (samples[-1].get('saved_bytes') or 0)>0):reasons.append('缺少明确正常停止及已保存音轨证据')
    if case=='asr_recovery':
        recovered=any(s.get('asr') in ('running','waiting','behind') and (s.get('covered_seconds') or 0)>(p.get('covered_seconds') or 0) for i,p in enumerate(samples) if p.get('asr') in ('failed','stopped') for s in samples[i+1:])
        if not recovered:reasons.append('缺少识别异常后确认覆盖增长证据')
    return {'case':case,'label':label,'eligible':not reasons,'reasons':reasons}


class AcceptanceReview(BaseModel):
    case:Case
    evidence_fingerprint:str=Field(pattern='^[0-9a-f]{64}$')
    verdict:Literal['passed','failed']
    real_device:bool
    audio_reviewed:bool


class RecordingAcceptance:
    def __init__(self,checks,environment_provider=environment):
        self.checks=checks;self.environment=environment_provider
        self.available=checks.available
        if not self.available:return
        try:
            with checks.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS acceptance_reviews(check_id TEXT,case_id TEXT,fingerprint TEXT,environment TEXT,verdict TEXT,real_device INTEGER,audio_reviewed INTEGER,reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(check_id,case_id))')
        except (OSError,sqlite3.Error):self.available=False
    def assessment(self,cid):
        if not self.available:raise OSError('验收记录暂不可用')
        report=self.checks.report(cid);env=self.environment();binding=fingerprint({'report':report,'environment':env})
        with self.checks.connect() as db:reviews={r['case_id']:dict(r) for r in db.execute('SELECT * FROM acceptance_reviews WHERE check_id=?',(cid,))}
        cases=[]
        for case in CASES:
            item=evaluate(report,case,env);review=reviews.get(case);current=bool(review and review['fingerprint']==binding and review['environment']==env)
            item['state']='passed' if current and item['eligible'] and review['verdict']=='passed' else 'failed' if current and review['verdict']=='failed' else 'stale' if review and not current else 'needs_review' if item['eligible'] else 'insufficient_evidence'
            item['reviewed_at']=review['reviewed_at'] if review else None;cases.append(item)
        return {'schema_version':1,'evidence_fingerprint':binding,'environment_fingerprint':env,'cases':cases,
            'review_scope':'user_attested_real_device_and_audio_review','automatic_hardware_certification':False}
    def review(self,cid,body):
        with self.checks.lock:
            status=self.assessment(cid)
            if status['evidence_fingerprint']!=body.evidence_fingerprint:raise ValueError('观察证据或运行版本已变化，请刷新后重新核对')
            item=next(c for c in status['cases'] if c['case']==body.case)
            if body.verdict=='passed' and (not item['eligible'] or not body.real_device or not body.audio_reviewed):raise ValueError('通过须有完整步骤证据，并确认真实设备及音轨回听')
            if self.checks.get(cid)['state']=='observing':raise ValueError('请先结束观察再保存验收')
            with self.checks.connect() as db:
                db.execute('INSERT OR REPLACE INTO acceptance_reviews(check_id,case_id,fingerprint,environment,verdict,real_device,audio_reviewed) VALUES(?,?,?,?,?,?,?)',(cid,body.case,status['evidence_fingerprint'],status['environment_fingerprint'],body.verdict,int(body.real_device),int(body.audio_reviewed)))
            return self.assessment(cid)
    def inventory(self):
        if not self.available:raise OSError('验收记录暂不可用')
        results=[self.assessment(row['id']) for row in self.checks.list()]
        return [{'case':key,'label':value[0],'state':next((c['state'] for r in results for c in r['cases'] if c['case']==key and c['state'] in ('passed','failed','stale')),'pending')} for key,value in CASES.items()]


def register_acceptance(app,acceptance):
    def call(fn,*args):
        try:return fn(*args)
        except LookupError as error:raise HTTPException(404,str(error))
        except ValueError as error:raise HTTPException(409,str(error))
        except (OSError,sqlite3.Error):raise HTTPException(503,'验收记录暂不可用，录音不受此工具控制')
    @app.get('/api/runtime/recording-checks/{cid}/acceptance')
    def assessment(cid:str):return JSONResponse(call(acceptance.assessment,cid),headers={'Cache-Control':'no-store'})
    @app.post('/api/runtime/recording-checks/{cid}/acceptance-review')
    def review(cid:str,body:AcceptanceReview):return call(acceptance.review,cid,body)
