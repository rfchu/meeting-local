"""Presentation-only grouping. Never changes segment IDs or subtitle ranges."""
import re

def continuous(left,right):
    return bool(left.get('continues_next') and right.get('continues_previous') and
        left.get('continuation_id') and left.get('continuation_id')==right.get('continuation_id') and
        abs(left['end']-right['start'])<=1/16000+.000001 and left.get('speaker','')==right.get('speaker',''))

def join_text(left,right):
    if not left or not right:return left+right
    return left+(' ' if re.match(r'[A-Za-z0-9]$',left[-1]) and re.match(r'^[A-Za-z0-9]',right[0]) else '')+right

def paragraphs(segments):
    result=[]
    for segment in segments:
        if result and continuous(result[-1]['parts'][-1],segment) and len(result[-1]['text'])+len(segment['text'])<=3000 and segment['end']-result[-1]['start']<=120:
            item=result[-1];item['text']=join_text(item['text'],segment['text']);item['end']=segment['end'];item['parts'].append(segment)
        else:result.append({**segment,'parts':[segment]})
    return result
