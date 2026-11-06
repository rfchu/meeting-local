"""Cancellable Whisper batches behind the same recording-bound event supervisor."""
import asyncio,json,math,tempfile,time
from pathlib import Path
import anyio
from .live_stream import inspect_audio,terminate
from .resources import request_for
from .db import uid

async def whisper_events(provider,cache,lock,payload,offset,rid,source,root,window):
    duration,silent=inspect_audio(payload);started=time.monotonic();aid=None;process=None;claim=request_for(lock,'live_asr',{'asr':1},rid)
    request_id=uid();seq=0
    def event(kind,**data):
        nonlocal seq
        result=json.dumps({'type':kind,'seq':seq,'request_id':request_id,'recording_id':rid,
            'engine':'whisper','start':offset,'end':offset+duration,**data},ensure_ascii=False)+'\n'
        seq+=1;return result
    try:
        aid,cached=cache.begin(rid,source,'whisper',offset,duration,payload,provider.identity())
        yield event('start',confirmed=True)
        if cached is not None:segments=cached
        elif silent:segments=[]
        else:
            if not claim.acquire(blocking=False):raise RuntimeError('识别资源正在使用，原音已保留')
            if not provider.ready():raise RuntimeError('Whisper 模型尚未就绪')
            with tempfile.TemporaryDirectory(prefix='meeting-batch-',dir=root) as work:
                folder=Path(work);audio=folder/'chunk.wav';output=folder/'result';audio.write_bytes(payload)
                with (folder/'worker.log').open('wb') as log:
                    with anyio.CancelScope(shield=True):
                        process=await asyncio.create_subprocess_exec(*provider.live_command(audio,output),stdout=log,stderr=log)
                    try:
                        await asyncio.wait_for(process.wait(),90)
                        if process.returncode:raise RuntimeError('Whisper 实时识别失败，原音已保留')
                        parts=provider.read_live_result(output)
                        segments=[]
                        for part in parts:
                            start,end=part['start'],part['end'];text=part['text']
                            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (start,end)) or start>end or not isinstance(text,str):raise ValueError('Whisper 返回了无效片段')
                            if text.strip():segments.append({'start':offset+max(0,min(duration,start)),
                                'end':offset+max(0,min(duration,end)),'text':text,
                                'boundary_reason':window.get('boundary_reason','standard')})
                    finally:
                        with anyio.CancelScope(shield=True):await terminate(process)
        if aid:
            cache.complete(aid,segments)
            segments=[dict(s,live_range_id=aid,live_segment_index=i) for i,s in enumerate(segments)]
        yield event('final',result={**window,'engine':'whisper','segments':segments,'duration':duration,
            'confirmed':True,'processing_ms':round((time.monotonic()-started)*1000),'first_text_ms':None,
            'persisted':True,'cached':cached is not None,'decoder':{}})
    except asyncio.CancelledError:
        if aid:cache.fail(aid,'实时会话中断，原音保留')
        raise
    except Exception as error:
        if aid:cache.fail(aid,'Whisper 识别未完成，原音保留')
        message='Whisper 识别超时，原音保留' if isinstance(error,TimeoutError) else str(error)
        yield event('error',message=message)
    finally:
        claim.close()
        if aid:cache.fail(aid,'实时请求未完成')
