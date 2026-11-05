"""PYTHONPATH=. .venv/bin/python scripts/evaluate_overview.py [--cases PATH]."""
import argparse,json,tempfile
from urllib.parse import urlparse
from pathlib import Path
from app.overview_evaluation import evaluate_cases

def main():
    parser=argparse.ArgumentParser(description='会中概览离线回归：不调用模型，不代表真实模型质量评测')
    parser.add_argument('--cases',type=Path,default=Path(__file__).resolve().parents[1]/'tests/fixtures/overview_cases.json')
    parser.add_argument('--model',help='显式指定本机模型后才调用服务；默认仅回归固定候选')
    parser.add_argument('--base-url',default='http://127.0.0.1:1234/v1')
    args=parser.parse_args()
    endpoint=urlparse(args.base_url)
    if endpoint.scheme not in ('http','https') or endpoint.hostname not in ('localhost','127.0.0.1','::1') or endpoint.username or endpoint.password:
        parser.error('评测入口仅允许本机回环服务地址，不读取真实会议或云端配置')
    with tempfile.TemporaryDirectory(prefix='overview-eval-') as root:
        result=evaluate_cases(json.loads(args.cases.read_text()),root,args.model,args.base_url)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
