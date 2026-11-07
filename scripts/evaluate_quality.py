"""Frozen synthetic evaluation. Default scoring is offline; local inference is explicit."""
import argparse,json,os,platform,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.quality_evaluation import FIXTURES,load_suite,offline,score_asr,compare,read_json,digest


def save(path,value):
    if path.exists() or path.is_symlink():raise ValueError('输出已存在，未覆盖报告或基线')
    with path.open('x',encoding='utf-8') as output:
        os.chmod(path,0o600);json.dump(value,output,ensure_ascii=False,indent=2);output.write('\n')


def run_local(engine,folder):
    from app.providers import get_asr_provider
    from scripts.manage_service import MacService
    corpus,_,_=load_suite(folder);provider=get_asr_provider(engine)
    if MacService().busy():raise ValueError('有正式录音或后台任务，请空闲时再运行本地模型评测')
    from app.quality_evaluation import model_input_status
    if model_input_status(provider)=='cloud_placeholder':raise ValueError('模型或配置为云端占位文件，请先在 Finder 保持下载到本机；本轮未启动模型或自动取回')
    if not provider.ready():raise ValueError('所选本地语音模型尚未就绪；不会下载或替换模型')
    identity=digest(provider.identity());outputs={};timings=[]
    with tempfile.TemporaryDirectory(prefix='tingji-f1-asr-') as work:
        def run(args):
            env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
            with (Path(work)/'model.log').open('ab') as log:subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=300,check=True)
        for case in corpus['cases']:
            if case['track']!='asr_text':continue
            if MacService().busy():raise ValueError('正式任务已开始，未提交下一条评测请求')
            started=time.monotonic();audio=Path(folder)/'audio'/case['audio']
            segments=provider.transcribe(audio,Path(work)/case['id'],run)
            outputs[case['id']]=''.join(s['text'] for s in segments)
            timings.append({'id':case['id'],'processing_seconds':round(time.monotonic()-started,3)})
            print(json.dumps({'case':case['id'],'engine':engine,'completed':True}),file=sys.stderr,flush=True)
        if digest(provider.identity())!=identity:raise ValueError('评测期间模型身份发生变化，请重新运行')
    from app.release_readiness import source_digest
    subject={'implementation_fingerprint':source_digest(ROOT),'engine':engine,'model_label':provider.label,'provider_identity_fingerprint':identity,'identity_scope':'provider_paths_sizes_mtimes_not_weight_checksum',
        'generation':'local_model_on_fixed_TTS_audio','request_mode':'production_transcribe_separate_process_per_sample','machine':platform.machine(),'macos':platform.mac_ver()[0]}
    report=score_asr(outputs,subject,folder,include_text=True)
    report['timings']=timings
    # Timings are deliberately excluded from pass/fail criteria, but included in report integrity.
    report['report_fingerprint']=digest({k:v for k,v in report.items() if k!='report_fingerprint'})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fixtures',type=Path,default=FIXTURES)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('validate')
    p=sub.add_parser('offline');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('asr');p.add_argument('--engine',choices=['qwen','whisper'],required=True);p.add_argument('--run-local-models',action='store_true');p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('score-asr');p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('compare');p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cer-tolerance',type=float,default=.005)
    args=parser.parse_args()
    try:
        if args.action=='validate':
            corpus,_,fingerprint=load_suite(args.fixtures);print(json.dumps({'valid':True,'suite':corpus['suite_id'],'cases':len(corpus['cases']),'fingerprint':fingerprint}));return
        if args.output.exists() or args.output.is_symlink():raise ValueError('输出已存在，未覆盖报告或基线')
        if args.action=='offline':result=offline(args.fixtures)
        elif args.action=='asr':
            if not args.run_local_models:raise ValueError('调用本地模型须显式指定 --run-local-models；不调用云端配置')
            result=run_local(args.engine,args.fixtures)
        elif args.action=='score-asr':
            data=read_json(args.input)
            result=score_asr(data['outputs'],{'generation':'imported_outputs_not_independently_executed'},args.fixtures)
        else:result=compare(read_json(args.baseline),read_json(args.candidate),args.cer_tolerance)
        save(args.output,result)
        print(json.dumps({key:result[key] for key in ('mode','metrics','regression_gate_passed','regressions') if key in result},ensure_ascii=False))
        if result.get('regression_gate_passed') is False or result.get('mode')=='offline_protocol' and result['metrics']['probe_pass_rate']<1:raise SystemExit(1)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        # Model stdout/errors can contain text or local paths; only categorical failures leave this command.
        parser.exit(2,'评测未完成：'+(str(error) if isinstance(error,ValueError) else type(error).__name__)+'\n')

if __name__=='__main__':main()
