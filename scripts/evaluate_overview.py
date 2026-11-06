"""PYTHONPATH=. .venv/bin/python scripts/evaluate_overview.py [--cases PATH]."""
import argparse,json,tempfile,os,sys
from urllib.parse import urlparse
from pathlib import Path
from app.overview_evaluation import evaluate_cases,rescore_report

def main():
    parser=argparse.ArgumentParser(description='会中概览评测：默认离线，显式指定模型才调用本机服务')
    parser.add_argument('--cases',type=Path,default=Path(__file__).resolve().parents[1]/'tests/fixtures/overview_cases.json')
    parser.add_argument('--model',help='显式指定本机模型后才调用服务；默认仅回归固定候选')
    parser.add_argument('--base-url',default='http://127.0.0.1:1234/v1')
    parser.add_argument('--case',help='只运行指定样例')
    parser.add_argument('--output',type=Path,help='保存评测报告，包含合成样例的实际输出，不含认证信息')
    parser.add_argument('--progress',action='store_true',help='逐轮输出耗时与通过情况')
    parser.add_argument('--rescore-report',type=Path,help='仅重新核对已有报告的事实规则，不调用模型')
    parser.add_argument('--previous-cases',type=Path,help='复评时提供与原报告指纹一致的旧样例')
    args=parser.parse_args()
    endpoint=urlparse(args.base_url)
    if endpoint.scheme not in ('http','https') or endpoint.hostname not in ('localhost','127.0.0.1','::1') or endpoint.username or endpoint.password:
        parser.error('评测入口仅允许本机回环服务地址，不读取真实会议或云端配置')
    with tempfile.TemporaryDirectory(prefix='overview-eval-') as root:
        cases=json.loads(args.cases.read_text())
        if args.case:
            cases=[case for case in cases if case['id']==args.case]
            if not cases:parser.error('指定样例不存在')
        progress=(lambda row:print(json.dumps({k:row[k] for k in ('case','round','generation','elapsed_seconds','passed')},ensure_ascii=False),file=sys.stderr,flush=True)) if args.progress else None
        if args.rescore_report:
            if args.model or not args.previous_cases:parser.error('复评不能指定模型，且必须提供 --previous-cases')
            result=rescore_report(json.loads(args.rescore_report.read_text()),cases,json.loads(args.previous_cases.read_text()))
        else:result=evaluate_cases(cases,root,args.model,args.base_url,os.getenv('MEETING_LLM_API_KEY','') if args.model else '',progress)
    if args.output:args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
