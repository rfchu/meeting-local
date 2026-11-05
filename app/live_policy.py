"""Pure, bounded policies for optional live previews; capture remains independent."""
import math

def sentence_hint(text,seconds):
    # Decoder punctuation is only a hint. An acoustic pause is still mandatory.
    return seconds if text.rstrip().rstrip('”’\"\'）)]').endswith(('。','！','？','!','?')) else None

def update_load(previous,processing_ms,duration,backlog_seconds):
    old=previous or {}
    ratio=max(0,min(4,processing_ms/1000/max(.5,duration)))
    ewma=round(.7*old['rtf']+.3*ratio,3) if 'rtf' in old else round(ratio,3)
    pressure=ewma>.75 or backlog_seconds>20
    slow=min(3,old.get('slow_rounds',0)+1) if pressure else 0
    fast=min(4,old.get('fast_rounds',0)+1) if ewma<.4 and backlog_seconds<6 else 0
    mode=old.get('mode','balanced')
    if slow>=2:mode='saving'
    elif fast>=3:mode='balanced'
    step=max(2,min(5,math.ceil(processing_ms/1000*1.3)))
    if mode=='saving':step=max(6,min(10,step+3))
    return {'mode':mode,'rtf':ewma,'slow_rounds':slow,'fast_rounds':fast,
            'preview_step_seconds':step,'backlog_seconds':round(max(0,backlog_seconds),2)}
