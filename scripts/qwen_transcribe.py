"""Fully local Qwen ASR + forced alignment in a disposable MLX process."""
import argparse,gc,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HOME']=str(ROOT/'models/hf-cache')
from app.alignment import aligned_segments


MAX_TOKENS=4096
MIN_RETRY_SECONDS=20


def quiet_split(samples,rate):
    """Choose a low-energy cut near the middle, away from both edges."""
    import numpy as np
    total=len(samples)
    middle=total//2
    radius=min(int(10*rate),max(0,total//2-int(15*rate)))
    start=max(int(15*rate),middle-radius)
    stop=min(total-int(15*rate),middle+radius)
    if stop<=start:return middle
    step=max(1,int(.1*rate));half=max(1,int(.1*rate))
    candidates=range(start,stop+1,step)
    return min(candidates,key=lambda i:(float(np.mean(np.square(samples[max(0,i-half):min(total,i+half)]))),abs(i-middle)))


def transcribe_adaptive(model,samples,rate,max_tokens=MAX_TOKENS,depth=0):
    """Retry a capped decode as smaller audio pieces using the loaded model."""
    result=model.generate(samples,language='Chinese',max_tokens=max_tokens,temperature=0.0,verbose=False)
    tokens=int(getattr(result,'generation_tokens',0) or 0)
    duration=len(samples)/rate
    attempt={'depth':depth,'duration':duration,'generation_tokens':tokens,'capped':tokens>=max_tokens}
    if tokens<max_tokens:return result.text.strip(),[attempt]
    if duration<=MIN_RETRY_SECONDS:
        raise RuntimeError('千问在短音频上仍达到输出长度上限，请检查该处是否为噪声或异常音频')
    cut=quiet_split(samples,rate)
    if cut<=0 or cut>=len(samples):raise RuntimeError('千问无法为超长输出找到可靠的重试分割点')
    left,left_attempts=transcribe_adaptive(model,samples[:cut],rate,max_tokens,depth+1)
    right,right_attempts=transcribe_adaptive(model,samples[cut:],rate,max_tokens,depth+1)
    return '\n'.join(x for x in (left,right) if x),[attempt,*left_attempts,*right_attempts]


def main():
    p=argparse.ArgumentParser()
    for name in ('model','aligner','audio','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--draft',action='store_true',help='实时草稿跳过强制对齐，时间戳仅表示音频块范围')
    args=p.parse_args()
    for directory in (args.model,args.aligner):
        if not Path(directory).is_dir():raise RuntimeError('只接受已下载的本地模型目录')
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from mlx_audio.stt import load
    samples,rate=sf.read(args.audio,dtype='float32')
    if rate!=16000 or samples.ndim!=1:raise RuntimeError('千问输入必须是 16kHz 单声道音频')
    duration=len(samples)/rate
    if duration>280:raise RuntimeError('千问对齐单块音频过长，请使用 120 秒分段')
    started=time.monotonic()
    mx.random.seed(0)
    model=load(str(Path(args.model).resolve()))
    text,attempts=transcribe_adaptive(model,samples,rate)
    output=Path(args.output)
    raw={'engine':'qwen','model':Path(args.model).name,'text':text,'duration':duration,'asr_attempts':attempts}
    output.with_suffix('.raw.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
    del model;gc.collect();mx.clear_cache()
    items=[]
    if not args.draft and text and any(c.isalnum() for c in text):
        aligner=load(str(Path(args.aligner).resolve()))
        alignment=aligner.generate(audio=samples,text=text,language='Chinese')
        items=[{'text':x.text,'start':x.start_time,'end':x.end_time} for x in alignment]
        del aligner,alignment;gc.collect();mx.clear_cache()
    raw['alignment']=items
    output.with_suffix('.raw.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
    segments=([{'start':0,'end':duration,'text':text,'speaker':'未分配'}] if text else []) if args.draft else aligned_segments(text,items,duration)
    raw['timestamp_mode']='chunk' if args.draft else 'forced_alignment'
    payload={**raw,'segments':segments,'elapsed_seconds':time.monotonic()-started}
    temp=output.with_suffix('.tmp');temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2));temp.replace(output)
    print(json.dumps({'engine':'qwen','segments':len(segments),'seconds':payload['elapsed_seconds'],'peak_memory_gb':mx.get_peak_memory()/1e9}),flush=True)

if __name__=='__main__':main()
