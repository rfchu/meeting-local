"""Local Qwen NDJSON transport; partials never enter durable success coverage."""
import asyncio,io,json,tempfile,time,wave
from pathlib import Path
import anyio
from .db import uid

def inspect_audio(payload):
    if not payload or len(payload)>4*1024*1024:raise ValueError('实时音频为空或超过 4MB')
    try:
        with wave.open(io.BytesIO(payload),'rb') as wav:
            duration=wav.getnframes()/wav.getframerate()
            valid=wav.getframerate()==16000 and wav.getnchannels()==1 and wav.getsampwidth()==2
            samples=wav.readframes(wav.getnframes())
            valid=valid and len(samples)==wav.getnframes()*2
    except (wave.Error,EOFError,ZeroDivisionError):raise ValueError('实时音频必须是有效的 16 kHz 单声道 PCM WAV')
    if not valid or not .5<=duration<=45:raise ValueError('实时音频必须是 16 kHz 单声道 PCM，时长 0.5–45 秒')
    return duration,not any(samples)

async def terminate(process):
    if not process or process.returncode is not None:return
    try:process.terminate()
    except ProcessLookupError:return
    try:await asyncio.wait_for(process.wait(),3)
    except asyncio.TimeoutError:
        try:process.kill()
        except ProcessLookupError:pass
        await process.wait()

async def qwen_events(provider,cache,lock,payload,offset,rid,source,root,window=None):
    duration,silent=inspect_audio(payload);started=time.monotonic();aid=None;locked=False;process=None
    request_id=uid();sequence=0;last_text=None
    def event(kind,**data):
        nonlocal sequence
        item={'type':kind,'seq':sequence,'request_id':request_id,'recording_id':rid,'engine':'qwen',
              'start':offset,'end':offset+duration,**data}
        sequence+=1;return json.dumps(item,ensure_ascii=False)+'\n'
    try:
        cached=None
        if rid:aid,cached=cache.begin(rid,source,'qwen',offset,duration,payload,provider.identity())
        yield event('start')
        if cached is not None:segments=cached
        elif silent:segments=[]
        else:
            if not lock.acquire(blocking=False):raise RuntimeError('上一段实时草稿仍在识别，请稍后重试')
            locked=True
            if not provider.ready():raise RuntimeError('Qwen 实时模型尚未就绪')
            if not hasattr(provider,'stream_command'):raise RuntimeError('当前 Qwen 运行库不支持流式接口，请切换整批输出')
            with tempfile.TemporaryDirectory(prefix='meeting-stream-',dir=root) as folder_name:
                folder=Path(folder_name);audio=folder/'chunk.wav';output=folder/'result.qwen.json';audio.write_bytes(payload)
                try:
                    with (folder/'stderr.log').open('wb') as stderr:
                        with anyio.CancelScope(shield=True):
                            process=await asyncio.create_subprocess_exec(*provider.stream_command(audio,output),
                                stdout=asyncio.subprocess.PIPE,stderr=stderr,limit=256*1024)
                        async with asyncio.timeout(90):
                            while line:=await process.stdout.readline():
                                try:item=json.loads(line)
                                except (ValueError,UnicodeDecodeError):continue
                                if item.get('type')=='qwen_partial':
                                    text=item.get('text')
                                    if not isinstance(text,str) or len(text)>20000:raise RuntimeError('流式文字超出有效范围')
                                    last_text=text
                                    yield event('partial',text=text)
                            await process.wait()
                        if process.returncode:raise RuntimeError('Qwen 流式识别失败：'+(folder/'stderr.log').read_text(errors='replace')[-800:])
                        result=json.loads(output.read_text())
                        if last_text is not None and ''.join(s['text'] for s in result['segments']).strip()!=last_text.strip():
                            raise RuntimeError('流式文字与最终结果不一致，不能确认为完整覆盖')
                        segments=[{'start':offset+max(0,min(duration,s['start'])),
                                   'end':offset+max(0,min(duration,s['end'])),'text':s['text']} for s in result['segments'] if s['text'].strip()]
                finally:
                    with anyio.CancelScope(shield=True):await terminate(process)
        if aid:cache.complete(aid,segments)
        result={**(window or {}),'segments':segments,'duration':duration,'engine':'qwen',
                'processing_ms':round((time.monotonic()-started)*1000),'cached':cached is not None,'persisted':bool(rid)}
        yield event('final',result=result)
    except asyncio.CancelledError:
        if aid:cache.fail(aid,'流式连接中断，部分文字未确认为完整覆盖')
        raise
    except Exception as error:
        message='Qwen 流式识别超时，部分文字未保存为完整结果' if isinstance(error,TimeoutError) else str(error)
        if aid:cache.fail(aid,message)
        yield event('error',message=message)
    finally:
        try:
            with anyio.CancelScope(shield=True):await terminate(process)
        finally:
            if locked:lock.release()
            if aid:cache.fail(aid,'流式请求未完成')
