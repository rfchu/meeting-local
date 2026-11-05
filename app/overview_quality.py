"""Conservative identity and bounded selection for a live overview, not a transcript."""
import re
import unicodedata

LIMITS={'topic':1,'point':4,'decision':3,'question':3,'action':3}
PENDING_LIMIT=10


def content_key(content):
    text=unicodedata.normalize('NFKC',content).lower()
    # Keep numeric spelling separately: stripping punctuation must not merge 1.5 and 15.
    return (''.join(c for c in text if c.isalnum()),tuple(re.findall(r'\d+(?:[.,]\d+)*',text)))


def same_item(left,right):
    if left['kind']!=right['kind'] or content_key(left['content'])!=content_key(right['content']):return False
    # Identical wording at unrelated source positions is not enough to merge people/tasks.
    return bool({e['segment_id'] for e in left['evidence']} & {e['segment_id'] for e in right['evidence']})


def select_items(incoming,carried,pinned,maximum=20):
    """New results take priority; unresolved actions/questions survive ordinary omissions.

    Confirmed items are immutable and exempt from the current-focus limits.
    Overflow remains available in earlier snapshots and the original transcript.
    """
    chosen=[];duplicates=0;overflow=0;counts={};remaining=min(PENDING_LIMIT,max(0,maximum-len(pinned)))
    # Carry action/question candidates before old descriptive points when space is scarce.
    carried=sorted(carried,key=lambda item:0 if item['kind'] in ('action','question') else 1)
    for item in incoming+carried:
        if any(same_item(item,old) for old in pinned+chosen):duplicates+=1;continue
        kind=item['kind']
        if len(chosen)>=remaining or counts.get(kind,0)>=LIMITS[kind]:overflow+=1;continue
        chosen.append(item);counts[kind]=counts.get(kind,0)+1
    return pinned+chosen,{'duplicates_removed':duplicates,'overflow_count':overflow}
