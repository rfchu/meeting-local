"""Map real Qwen word/character timestamps back to punctuation-preserving text.

Independent of MLX, so edge cases are testable on any Python interpreter.
"""
import math,re


def normalize(text):
    return ''.join(c.lower() for c in text if c.isalnum() or c=="'")


def aligned_segments(text,items,duration,max_chars=48,max_seconds=15):
    if not normalize(text): return []
    if not items: raise ValueError('千问对齐模型未返回时间戳，已保留原始文字供排查')
    indexes=[i for i,c in enumerate(text) if c.isalnum() or c=="'"]
    target=normalize(text)
    normalized=[normalize(item['text']) for item in items]
    if ''.join(normalized)!=target:
        raise ValueError('千问文字与对齐结果不一致，不能生成可靠时间戳')
    units=[];offset=0;previous_start=0.0
    for item,token in zip(items,normalized):
        if not token:continue
        start=float(item['start']);end=float(item['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<start or start>duration+.1 or end>duration+1:
            raise ValueError('千问对齐模型返回超出音频范围的时间戳')
        if start<previous_start-.1: raise ValueError('千问对齐时间戳顺序异常')
        start=min(duration,max(start,previous_start));previous_start=start
        begin=0 if offset==0 else indexes[offset]
        offset+=len(token)
        finish=indexes[offset] if offset<len(indexes) else len(text)
        units.append({'start':start,'end':min(duration,max(start,end)),'text':text[begin:finish]})
    segments=[];group=[]
    for i,unit in enumerate(units):
        group.append(unit)
        content=''.join(u['text'] for u in group)
        terminal=bool(re.search(r'[。！？!?；;][”’\"\')）\s]*$',unit['text']))
        # Decimal points and English abbreviations do not force a split.
        pause=(i+1<len(units) and units[i+1]['start']-unit['end']>=.8)
        if terminal or pause or len(content)>=max_chars or unit['end']-group[0]['start']>=max_seconds or i==len(units)-1:
            segments.append({'start':group[0]['start'],'end':max(x['end'] for x in group),
                             'text':content.strip(),'speaker':'未分配'})
            group=[]
    return segments


def aligned_or_chunk(text,items,duration):
    """Keep decoded text when alignment is unusable, without inventing word times."""
    try:return aligned_segments(text,items,duration),None
    except (ValueError,TypeError,KeyError,OverflowError) as error:
        if not math.isfinite(duration) or duration<=0:raise ValueError('音频时长无效')
        segments=[{'start':0,'end':duration,'text':text.strip(),'speaker':'未分配',
                   'timestamp_precision':'chunk','alignment_warning':'对齐失败，仅保留音频块范围；请回听校准'}] if text.strip() else []
        return segments,str(error)
