"""Versioned synthetic quality baselines. Pure scoring never contacts models or meetings."""
import hashlib,json,math,tempfile,time,unicodedata,wave
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'evaluation/fixtures'
TRACKS={'qa_guard','minutes_guard','segmentation','speakers','asr_text'}
SCORER_VERSION=1


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def file_digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def rubric_digest():return file_digest(Path(__file__))


def read_json(path):
    if path.is_symlink() or path.stat().st_size>4*1024*1024:raise ValueError('评测文件为链接或超出大小限制')
    return json.loads(path.read_text())


def load_suite(folder=FIXTURES):
    folder=Path(folder);corpus=read_json(folder/'corpus-v1.json')
    if corpus.get('schema_version')!=1 or corpus.get('scope')!='self_authored_synthetic_only':raise ValueError('评测集版本或来源范围不支持')
    rows=corpus['cases'];ids=[row['id'] for row in rows]
    if not rows or len(rows)>500 or len(ids)!=len(set(ids)):raise ValueError('评测样本缺失或 ID 重复')
    if set(row['track'] for row in rows)!=TRACKS:raise ValueError('评测集必须包含所有固定评测类别')
    for row in rows:
        if not isinstance(row['id'],str) or not row['id'] or len(row['id'])>100:raise ValueError('样本 ID 无效')
        if row['track'] in ('qa_guard','minutes_guard') and type(row.get('expected_accept')) is not bool:raise ValueError('引用探针须有明确预期')
        if row['track']=='asr_text':
            name=row['audio']
            if not isinstance(name,str) or Path(name).name!=name or not name.endswith('.wav'):raise ValueError('音频文件必须位于固定样本目录')
            audio=folder/'audio'/name
            if audio.is_symlink() or file_digest(audio)!=row['audio_sha256']:raise ValueError('固定音频指纹不一致')
            if not isinstance(row['reference'],str) or not 1<=len(row['reference'])<=2000 or not normalize(row['reference']):raise ValueError('ASR 参考文字无效')
            with wave.open(str(audio),'rb') as wav:
                if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,16000) or not 0<wav.getnframes()<=60*16000:raise ValueError('固定音频格式或时长无效')
    overview_name=corpus['overview_file']
    if Path(overview_name).name!=overview_name:raise ValueError('概览样本路径无效')
    overview=read_json(folder/overview_name)
    return corpus,overview,digest({'corpus':corpus,'overview':overview})


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if not c.isspace() and not unicodedata.category(c).startswith('P'))


def distance(reference,hypothesis):
    previous=list(range(len(hypothesis)+1))
    for i,a in enumerate(reference,1):
        current=[i]
        for j,b in enumerate(hypothesis,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        previous=current
    return previous[-1]


def envelope(corpus,suite_hash,mode,rows,metrics,subject):
    value={'schema_version':1,'suite_id':corpus['suite_id'],'suite_fingerprint':suite_hash,'rubric_version':SCORER_VERSION,
        'rubric_fingerprint':rubric_digest(),'scope':corpus['scope'],'mode':mode,'subject':subject,
        'created_utc':datetime.now(timezone.utc).isoformat(),'rows':rows,'metrics':metrics,
        'limitations':['合成样本，不代表真实长会议／多说话人／噪声质量','引用存在不自动证明语义充分','CER 保留数值书写差异，不进行中文数字等价折算；延迟仅作观察']}
    value['report_fingerprint']=digest(value);return value


def offline(folder=FIXTURES):
    from .qa import Answer,validate_answer
    from .minutes import MeetingMinutes,validate_minutes_citations
    from .speaker_evaluation import evaluate_speakers
    from .live_segmentation import elastic_plan
    from .overview_evaluation import evaluate_cases
    corpus,overview,suite_hash=load_suite(folder);rows=[]
    for case in corpus['cases']:
        track=case['track'];detail={}
        if track=='asr_text':continue
        if track in ('qa_guard','minutes_guard'):
            accepted=True
            try:
                if track=='qa_guard':validate_answer(Answer.model_validate(case['candidate']),case['sources'])
                else:validate_minutes_citations(MeetingMinutes.model_validate(case['candidate']),set(case['valid_ids']))
            except ValueError:accepted=False
            passed=accepted==case['expected_accept'];detail={'accepted':accepted,'expected_accept':case['expected_accept']}
        elif track=='segmentation':
            voiced=case['voiced_seconds'];quiet=case['quiet_seconds']
            if not all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=25 for x in (voiced,quiet)):raise ValueError('分段样本时长无效')
            raw=b'\x70\x17'*round(voiced*16000)+b'\0\0'*round(quiet*16000)
            plan=elastic_plan(raw,case['target'],case['forced']);expected=case['expected']
            passed=bool(plan and plan['confirmed']==expected['confirmed'] and plan['boundary_reason']==expected['boundary_reason'] and abs(plan['count']/16000-expected['seconds'])<.03)
            detail={'confirmed':plan['confirmed'],'boundary_reason':plan['boundary_reason']} if plan else {'ready':False}
        elif track=='speakers':
            score=evaluate_speakers(case['data']);expected=case['expected']
            passed=score['changes']['fixed']==expected['fixed'] and score['changes']['harmful']==expected['harmful'] and score['proposed']['correct_segments']==expected['correct_segments']
            detail={'fixed':score['changes']['fixed'],'harmful':score['changes']['harmful'],'metric':'fixed_segment_labels_not_DER'}
        rows.append({'id':case['id'],'track':track,'passed':passed,**detail})
    with tempfile.TemporaryDirectory(prefix='tingji-f1-offline-') as root:result=evaluate_cases(overview,root)
    for row in result['rounds']:rows.append({'id':'overview:'+row['case']+':'+str(row['round']),'track':'overview_pipeline','passed':row['passed'],'checks':row['checks']})
    from .release_readiness import source_digest
    return envelope(corpus,suite_hash,'offline_protocol',rows,{'probe_count':len(rows),'passed_probes':sum(r['passed'] for r in rows),'probe_pass_rate':sum(r['passed'] for r in rows)/len(rows),'model_calls':0},{'implementation_fingerprint':source_digest(ROOT),'generation':'fixed_candidates_not_model_outputs'})


def score_asr(outputs,subject,folder=FIXTURES,include_text=False):
    corpus,_,suite_hash=load_suite(folder);cases=[c for c in corpus['cases'] if c['track']=='asr_text']
    if not isinstance(outputs,dict) or set(outputs)!={c['id'] for c in cases}:raise ValueError('ASR 结果必须包含全部且仅包含固定样本 ID，不能静默跳过失败样本')
    rows=[]
    for case in cases:
        text=outputs[case['id']]
        if not isinstance(text,str) or len(text)>8000:raise ValueError('ASR 输出类型或长度无效')
        ref=normalize(case['reference']);hyp=normalize(text);errors=distance(ref,hyp)
        rows.append({'id':case['id'],'track':'asr_text','reference_characters':len(ref),'hypothesis_characters':len(hyp),'character_errors':errors,'cer':errors/len(ref),'output_fingerprint':digest(text),'audio_sha256':case['audio_sha256'],**({'hypothesis':text} if include_text else {})})
    total=sum(r['reference_characters'] for r in rows);errors=sum(r['character_errors'] for r in rows)
    return envelope(corpus,suite_hash,'asr_text_quality',rows,{'samples':len(rows),'reference_characters':total,'character_errors':errors,'micro_cer':errors/total,'exact_normalized_samples':sum(r['character_errors']==0 for r in rows)},subject)


def check_report(report):
    if report.get('schema_version')!=1 or report.get('report_fingerprint')!=digest({k:v for k,v in report.items() if k!='report_fingerprint'}):raise ValueError('评测报告版本或指纹不一致')
    rows=report.get('rows',[])
    ids=[row['id'] for row in rows]
    if not ids or len(ids)!=len(set(ids)):raise ValueError('评测报告存在重复或缺失样本')
    metrics=report['metrics']
    if report['mode']=='offline_protocol':
        if any(type(row.get('passed')) is not bool for row in rows) or metrics['probe_count']!=len(rows) or metrics['passed_probes']!=sum(row['passed'] for row in rows) or metrics['probe_pass_rate']!=sum(row['passed'] for row in rows)/len(rows):raise ValueError('协议报告汇总与逐条结果不一致')
    elif report['mode']=='asr_text_quality':
        if any(type(row.get('character_errors')) is not int or row['character_errors']<0 or type(row.get('reference_characters')) is not int or row['reference_characters']<=0 for row in rows):raise ValueError('CER 报告计数无效')
        total=sum(row['reference_characters'] for row in rows);errors=sum(row['character_errors'] for row in rows)
        if metrics['samples']!=len(rows) or metrics['reference_characters']!=total or metrics['character_errors']!=errors or abs(metrics['micro_cer']-errors/total)>1e-12 or any(abs(row['cer']-row['character_errors']/row['reference_characters'])>1e-12 for row in rows):raise ValueError('CER 报告汇总与逐条结果不一致')
    else:raise ValueError('未知评测报告模式')
    return report


def compare(baseline,candidate,cer_tolerance=.005):
    check_report(baseline);check_report(candidate)
    if not math.isfinite(cer_tolerance) or not 0<=cer_tolerance<=1:raise ValueError('CER 容差无效')
    for key in ('suite_id','suite_fingerprint','rubric_version','rubric_fingerprint','mode','scope'):
        if baseline.get(key)!=candidate.get(key):raise ValueError('评测集、评分规则或评测模式不同，不可直接比较')
    if {r['id'] for r in baseline['rows']}!={r['id'] for r in candidate['rows']}:raise ValueError('候选报告样本不完整')
    mode=baseline['mode']
    if mode=='asr_text_quality':
        delta=candidate['metrics']['micro_cer']-baseline['metrics']['micro_cer'];regressions=['micro_cer'] if delta>cer_tolerance else []
    elif mode=='offline_protocol':
        delta=candidate['metrics']['probe_pass_rate']-baseline['metrics']['probe_pass_rate']
        old={r['id']:r for r in baseline['rows']};regressions=[r['id'] for r in candidate['rows'] if old[r['id']]['passed'] and not r['passed']]
    else:raise ValueError('未知评测报告模式')
    return {'schema_version':1,'comparable':True,'mode':mode,'baseline_report':baseline['report_fingerprint'],'candidate_report':candidate['report_fingerprint'],'delta':delta,'cer_tolerance':cer_tolerance if mode=='asr_text_quality' else None,'regressions':regressions,'regression_gate_passed':not regressions,'latency_is_informational':True,'subject_changed':baseline['subject']!=candidate['subject']}


def model_input_status(provider):
    # stat only: never read/rehydrate a dataless model or its config during preflight.
    import stat
    for row in provider.identity():
        path=Path(row['path'])
        if getattr(path.stat(),'st_flags',0)&getattr(stat,'SF_DATALESS',0x40000000):return 'cloud_placeholder'
    return 'ready' if provider.ready() else 'missing'


def baseline_inventory():
    corpus,_,suite_hash=load_suite();reports=[]
    for path in sorted((ROOT/'evaluation/baselines').glob('*.json')):
        try:
            value=check_report(read_json(path));current=value['suite_fingerprint']==suite_hash and value['rubric_fingerprint']==rubric_digest()
            reports.append({'name':path.stem,'mode':value['mode'],'current':current,'metrics':{k:v for k,v in value['metrics'].items() if k in ('probe_count','passed_probes','probe_pass_rate','model_calls','samples','reference_characters','character_errors','micro_cer','exact_normalized_samples')},'subject':{k:v for k,v in value['subject'].items() if k in ('engine','model_label','generation','request_mode','machine','macos','implementation_fingerprint','provider_identity_fingerprint','identity_scope')}})
        except (OSError,ValueError,KeyError,TypeError):reports.append({'name':path.stem,'current':False,'mode':'invalid'})
    from .providers import QwenMLX,WhisperCpp
    measured={r.get('subject',{}).get('engine') for r in reports if r.get('current') and r.get('mode')=='asr_text_quality'}
    pending=[]
    for provider in (QwenMLX(),WhisperCpp()):
        if provider.engine_id not in measured:
            pending.append({'engine':provider.engine_id,'label':provider.label,'reason':model_input_status(provider)})
    return {'schema_version':1,'passive':True,'models_started':False,'scope':corpus['scope'],'suite_id':corpus['suite_id'],'suite_fingerprint':suite_hash,'fixed_cases':len(corpus['cases']),'reports':reports,'pending_asr':pending}
