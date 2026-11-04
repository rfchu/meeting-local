"""Bounded acoustic pause policy; text punctuation never commits audio by itself."""
from array import array

RATE=16000
SOFT_SECONDS=15
HARD_SECONDS=25
PAUSE_SECONDS=.8
FRAME_SAMPLES=320
ENERGY=.003

def acoustic_pause(raw,minimum=3):
    samples=array('h');samples.frombytes(raw)
    quiet=0;leading=0;seen_voice=False;cut=None
    for start in range(0,len(samples),FRAME_SAMPLES):
        block=samples[start:start+FRAME_SAMPLES]
        if len(block)<FRAME_SAMPLES:break
        silent=sum(v*v for v in block)/len(block)<(ENERGY*32768)**2
        if silent:
            quiet+=len(block)
            if not seen_voice:leading+=len(block)
            end=start+len(block)
            if quiet>=round(PAUSE_SECONDS*RATE) and end>=round(minimum*RATE):cut=end;break
        else:quiet=0;seen_voice=True
    return cut,leading/RATE

def elastic_plan(raw,target,forced=None):
    raw=raw[:HARD_SECONDS*RATE*2]
    available=len(raw)//2
    minimum=.5 if forced else 3
    if available<round(minimum*RATE):return None
    cut,leading=acoustic_pause(raw,minimum)
    if cut is not None:count=cut;reason='silence';confirmed=True
    elif forced:count=available;reason=forced;confirmed=True
    elif available>=HARD_SECONDS*RATE:count=HARD_SECONDS*RATE;reason='hard_limit';confirmed=True
    elif available>=round(target*RATE):count=available;reason='preview';confirmed=False
    else:return None
    return {'count':min(count,HARD_SECONDS*RATE),'confirmed':confirmed,'boundary_reason':reason,
            'leading_silence_seconds':leading,'extending':not confirmed and available>=SOFT_SECONDS*RATE}
