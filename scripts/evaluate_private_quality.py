"""Explicit local scoring for frozen, privately held human annotations. Never calls a cloud API."""
import argparse,json,os,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.paths import RuntimePaths
from app.maintenance import DataLease
from app.quality_samples import QualitySamples
from app.quality_evaluation import digest,model_input_status,read_json


def run_local(samples,did,engine):
    from app.providers import get_asr_provider
    from scripts.manage_service import MacService
    data=samples.dataset(did);provider=get_asr_provider(engine)
    if MacService().busy():raise ValueError('正式录音或任务繁忙，请空闲后再评测')
    if model_input_status(provider)!='ready':raise ValueError('本机模型或配置未完整驻留，不调用模型')
    identity=digest(provider.identity());outputs={};timings=[]
    with tempfile.TemporaryDirectory(prefix='tingji-private-asr-') as folder:
        def run(args):
            env=os.environ.copy();env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
            with (Path(folder)/'model.log').open('ab') as log:subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=300,check=True,env=env)
        for member in data['members']:
            if MacService().busy():raise ValueError('正式任务已开始，未继续下一条评测')
            start=time.monotonic();rows=provider.transcribe(samples.verified_audio(member['id']),Path(folder)/member['id'],run)
            outputs[member['id']]=''.join(row['text'] for row in rows);timings.append(round(time.monotonic()-start,3))
        if digest(provider.identity())!=identity:raise ValueError('模型身份发生变化，请重新评测')
    samples.dataset(did)
    report=samples.score(did,outputs,{'engine':engine,'model_label':provider.label,'generation':'local_model_on_user_reviewed_real_speech','provider_identity_fingerprint':identity})
    report['processing_seconds']=timings;report['report_fingerprint']=digest({k:v for k,v in report.items() if k!='report_fingerprint'})
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data-root',type=Path,default=RuntimePaths.from_env().data);parser.add_argument('--dataset-id',required=True)
    group=parser.add_mutually_exclusive_group(required=True);group.add_argument('--engine',choices=['qwen','whisper']);group.add_argument('--predictions',type=Path)
    parser.add_argument('--run-local-models',action='store_true');args=parser.parse_args()
    try:
        if args.engine and not args.run_local_models:raise ValueError('本机模型评测须显式指定 --run-local-models')
        if not (args.data_root/'quality-samples/samples.sqlite3').is_file():raise ValueError('私有样本库不存在，未创建')
        with DataLease(args.data_root):
            samples=QualitySamples(args.data_root)
            report=run_local(samples,args.dataset_id,args.engine) if args.engine else samples.score(args.dataset_id,read_json(args.predictions)['outputs'],{'generation':'imported_outputs_not_independently_executed'})
            rid=samples.save_report(args.dataset_id,report)
        print(json.dumps({'private_report_id':rid,'metrics':report['metrics'],'reference_origin':report['reference_origin'],'raw_text_exported':False},ensure_ascii=False))
    except (OSError,ValueError,LookupError,KeyError,sqlite3.Error,subprocess.SubprocessError) as error:
        parser.exit(2,'私有评测未完成：'+(str(error) if isinstance(error,(ValueError,LookupError)) else type(error).__name__)+'\n')

if __name__=='__main__':main()
