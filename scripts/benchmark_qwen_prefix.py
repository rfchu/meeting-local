"""Compare warm prefix/full decoding using caller-provided *test* audio only.
PYTHONPATH=. .venv/bin/python scripts/benchmark_qwen_prefix.py --audio synthetic.wav
No app database is used; results include test transcript text.
"""
import argparse,asyncio,json,tempfile,time,wave
from pathlib import Path
from app.providers import QwenMLX
from app.qwen_resident import QwenResident
from app.live_session import stable_prefix,pcm_wav

async def benchmark(audio,output=None):
    with wave.open(str(audio),'rb') as source:
        if source.getframerate()!=16000 or source.getnchannels()!=1 or source.getsampwidth()!=2:raise ValueError('需要 16k 单声道 PCM 测试音频')
        raw=source.readframes(min(source.getnframes(),15*16000))
    duration=len(raw)/32000
    windows=[s for s in (3,6,9,12) if s<duration]+[duration]
    result=[]
    with tempfile.TemporaryDirectory(prefix='tingji-prefix-benchmark-') as folder:
        pool=QwenResident(folder);provider=QwenMLX()
        try:
            await pool.warm(provider)
            for enabled in (False,True):
                previous='';stable=''
                for i,seconds in enumerate([*windows,duration]):
                    confirmed=i==len(windows);path=Path(folder)/'input.wav';path.write_bytes(pcm_wav(raw[:round(seconds*16000)*2]))
                    started=time.monotonic();events=[event async for event in pool.generate(provider,path,context='参考词汇：听记。仅转写当前音频。',
                        continuation={'key':'benchmark-'+str(enabled),'enabled':enabled,'confirmed':confirmed,'previous_text':previous,'stable_text':stable})]
                    final=events[-1];text=''.join(s['text'] for s in final['segments']);stable=stable_prefix(previous,text);previous=text
                    result.append({'enabled':enabled,'seconds':seconds,'confirmed':confirmed,'processing_ms':round((time.monotonic()-started)*1000),
                                   'text':text,**final.get('decoder',{})})
        finally:await pool.shutdown()
    report={'windows':result,'final_text_equal':result[len(windows)]['text']==result[-1]['text'],
            'prefix_rounds':sum(r.get('mode')=='prefix' for r in result),'test_audio_only':True}
    if output:Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--audio',required=True);p.add_argument('--output');args=p.parse_args()
    asyncio.run(benchmark(args.audio,args.output))
