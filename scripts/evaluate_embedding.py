"""Offline synthetic retrieval smoke evaluation; not a real meeting quality benchmark."""
import argparse,json,time
from app.local_embedding import local_embedding

DOCUMENTS=[
 '产品排期会：星河项目原定周五交付，负责人李工。',
 '排期调整会：星河项目交付推迟到下周一，负责人保持李工。',
 '销售例会：远山客户本月回款已确认，合同归档由王工跟进。',
 '研发评审：移动端登录超时需要排查，陈工本周提交修复方案。',
 '采购沟通：服务器采购预算为五万元，由张工询价。',
 '个人备注：我担心星河项目延期，这只是个人判断，尚无会议决定。',
 '技术周会：数据库迁移暂缓，待完成备份演练后重新评审。',
 '会议记录：讨论了海风项目排期，但没有确定负责人和截止日期。',
]
CASES=[('星河项目什么时候交付？',[0,1]),('星河项目时间发生什么变化？',[0,1]),('谁负责星河项目交付？',[0,1]),
 ('远山客户钱到账了吗？',[2]),('谁处理登录超时故障？',[3]),('买服务器准备花多少钱？',[4]),('为什么先不迁数据库？',[6]),('海风项目有没有明确交付期限？',[7])]
def main():
 import numpy as np
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fp32-reference');args=parser.parse_args()
 model=local_embedding();model._load()
 if args.fp32_reference:
  from pathlib import Path
  from app.local_embedding import checksum
  if checksum(Path(args.fp32_reference))!='69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a':raise SystemExit('FP32 reference checksum mismatch')
  import onnxruntime as ort
  model._load();options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
  model.session=ort.InferenceSession(args.fp32_reference,sess_options=options,providers=['CPUExecutionProvider'])
 start=time.perf_counter();docs=np.asarray(model.encode(DOCUMENTS));queries=np.asarray(model.encode([q for q,_ in CASES],query=True));elapsed=time.perf_counter()-start
 recalls=[];ranks=[];rows=[]
 for (question,expected),query in zip(CASES,queries):
  ordered=np.argsort(-(docs@query)).tolist();rank=min(ordered.index(i)+1 for i in expected)
  recall=len(set(ordered[:3])&set(expected))/len(expected);recalls.append(recall);ranks.append(1/rank)
  rows.append({'question':question,'top3':ordered[:3],'expected':expected,'recall_at_3':recall})
 print(json.dumps({'scope':'synthetic smoke only; no real meeting accuracy claim','model':'bge-small-zh-v1.5-fp32-reference' if args.fp32_reference else model.status()['model'],'cases':len(CASES),'mean_recall_at_3':sum(recalls)/len(recalls),'mrr':sum(ranks)/len(ranks),'elapsed_seconds':round(elapsed,3),'rows':rows},ensure_ascii=False,indent=2));model.release()
if __name__=='__main__':main()
