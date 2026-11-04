"""One locally loaded ASR model; serial, request-bound stdin/stdout protocol."""
import argparse,json,gc,sys,time
from pathlib import Path
from qwen_transcribe import transcribe_streamed

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);args=parser.parse_args()
    if not Path(args.model).is_dir():raise RuntimeError('只接受本地模型目录')
    import mlx.core as mx
    import soundfile as sf
    from mlx_audio.stt import load
    started=time.monotonic();model=load(str(Path(args.model).resolve()))
    def send(value):print(json.dumps(value,ensure_ascii=False),flush=True)
    send({'type':'ready','load_ms':round((time.monotonic()-started)*1000),'memory_gb':mx.get_active_memory()/1e9,'peak_memory_gb':mx.get_peak_memory()/1e9})
    for line in sys.stdin:
        request=json.loads(line);rid=request['id']
        try:
            samples,rate=sf.read(request['audio'],dtype='float32')
            if rate!=16000 or samples.ndim!=1 or not .5<=len(samples)/rate<=45:raise RuntimeError('常驻识别只接受 0.5–45 秒单声道 16kHz 音频')
            mx.random.seed(0)
            text,attempts=transcribe_streamed(model,samples,rate,context=str(request.get('context',''))[:5000],emit=lambda event:send({**event,'id':rid}))
            duration=len(samples)/rate
            send({'type':'qwen_final','id':rid,'segments':[{'start':0,'end':duration,'text':text}] if text else [],
                  'peak_memory_gb':mx.get_peak_memory()/1e9,'tokens':attempts[0]['generation_tokens']})
            del samples;gc.collect();mx.clear_cache()
        except Exception as error:
            send({'type':'qwen_error','id':rid,'message':str(error)});gc.collect();mx.clear_cache()

if __name__=='__main__':main()
