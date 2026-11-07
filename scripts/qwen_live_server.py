"""One locally loaded ASR model; serial, request-bound stdin/stdout protocol."""
import argparse,json,gc,sys,time
from pathlib import Path
from qwen_transcribe import transcribe_streamed
from app.languages import source_language
from qwen_stream_decoder import MLXStreamDecoder,PrefixHistory,decode_with_fallback

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);args=parser.parse_args()
    if not Path(args.model).is_dir():raise RuntimeError('只接受本地模型目录')
    import mlx.core as mx
    import soundfile as sf
    from mlx_audio.stt import load
    started=time.monotonic();model=load(str(Path(args.model).resolve()));decoder=MLXStreamDecoder();history=PrefixHistory()
    def send(value):print(json.dumps(value,ensure_ascii=False),flush=True)
    send({'type':'ready','load_ms':round((time.monotonic()-started)*1000),'memory_gb':mx.get_active_memory()/1e9,'peak_memory_gb':mx.get_peak_memory()/1e9})
    for line in sys.stdin:
        request=json.loads(line);rid=request['id']
        try:
            samples,rate=sf.read(request['audio'],dtype='float32')
            if rate!=16000 or samples.ndim!=1 or not .5<=len(samples)/rate<=45:raise RuntimeError('常驻识别只接受 0.5–45 秒单声道 16kHz 音频')
            mx.random.seed(0)
            language=source_language(request.get('source_language'))
            context=str(request.get('context',''))[:5000];continuation=request.get('continuation') or {}
            history_context=language+'\n'+context
            prefix=history.choose(model._tokenizer,continuation,samples.tobytes(),history_context) if decoder.enabled and language!='auto' else []
            def decode(adapter,prefix_ids):
                mx.random.seed(0)
                return transcribe_streamed(model,samples,rate,context=context,language=language,decoder=adapter,prefix_tokens=prefix_ids,emit=lambda event:send({**event,'id':rid}))
            text,attempts,prefix,fallback_reason=decode_with_fallback(decode,decoder,prefix)
            if fallback_reason:history.clear()
            metrics={**(decoder.metrics if decoder.enabled else {}),'mode':'prefix' if prefix else 'full','prefix_tokens':len(prefix),
                     'generated_tokens':attempts[0]['generation_tokens'],'fallback':bool(fallback_reason),'fallback_reason':fallback_reason,
                     'final_verified':bool(continuation.get('confirmed',True)) and not prefix}
            history.update(continuation,samples.tobytes(),history_context,text)
            duration=len(samples)/rate
            send({'type':'qwen_final','id':rid,'segments':[{'start':0,'end':duration,'text':text}] if text else [],
                  'peak_memory_gb':mx.get_peak_memory()/1e9,'tokens':attempts[0]['generation_tokens'],'decoder':metrics})
            del samples;gc.collect();mx.clear_cache()
        except Exception as error:
            decoder.clear();history.clear()
            send({'type':'qwen_error','id':rid,'message':str(error)});gc.collect();mx.clear_cache()

if __name__=='__main__':main()
