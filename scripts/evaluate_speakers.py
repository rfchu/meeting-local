#!/usr/bin/env python3
"""Create an annotation template or score a local speaker-review fixture."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.speaker_evaluation import evaluate_speakers


def main():
    parser=argparse.ArgumentParser(description='本地发言人校对评估；不会修改会议数据或调用模型')
    parser.add_argument('--input',required=True,type=Path,help='评估 JSON；--template 时可使用文字稿 JSON（segments）')
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--template',action='store_true',help='生成待人工标注的 reference_labels，不自动视为正确答案')
    args=parser.parse_args()
    try:
        if args.input.resolve()==args.output.resolve() or args.output.exists():raise ValueError('输出文件已存在或与输入相同，请使用新路径')
        data=json.loads(args.input.read_text())
        if args.template:
            segments=data['segments']
            result={'segments':segments,'reference_labels':{s['id']:None for s in segments},'suggestions':[]}
        else:result=evaluate_speakers(data)
        with args.output.open('x') as output:json.dump(result,output,ensure_ascii=False,indent=2)
    except (ValueError,KeyError,OSError,TypeError) as error:parser.exit(1,f'评估失败：{error}\n')
    print(f'已保存：{args.output}')

if __name__=='__main__':main()
