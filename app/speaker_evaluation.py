"""Offline evaluation against independent manual labels, never production mutation.

Metrics cover speaker labels on fixed transcript segments. They are not DER:
boundary timing errors, missed speech and overlapping speech are not measured.
"""
import math
from collections import Counter


def evaluate_speakers(data):
    segments=data['segments'];references=data['reference_labels'];suggestions=data.get('suggestions',[])
    rows={s['id']:s for s in segments}
    if len(rows)!=len(segments):raise ValueError('片段 ID 重复')
    if not set(references).issubset(rows):raise ValueError('人工标注包含未知片段')
    annotated={sid:label for sid,label in references.items() if label is not None}
    if any(not isinstance(label,str) or not label.strip() for label in annotated.values()):raise ValueError('人工标注必须是非空发言人名称或 null')
    if not annotated:raise ValueError('至少需要一条独立人工标注；null 表示未标注，不计入评分')
    for row in segments:
        duration=float(row['end'])-float(row['start'])
        if not math.isfinite(duration) or duration<0:raise ValueError('片段时间无效')
    allowed={s['speaker'] for s in segments}
    if not set(annotated.values()).issubset(allowed):raise ValueError('人工标注应使用基线已有发言人名称')
    proposals={}
    for item in suggestions:
        sid=item['segment_id']
        if sid not in rows or sid in proposals:raise ValueError('建议片段未知或重复')
        if item['proposed_speaker'] not in allowed:raise ValueError('建议使用了未登记的发言人名称')
        if rows[sid].get('speaker_locked') and item['proposed_speaker']!=rows[sid]['speaker']:raise ValueError('建议修改了已确认片段')
        if item.get('original_speaker',rows[sid]['speaker'])!=rows[sid]['speaker']:raise ValueError('建议与基线版本不一致')
        proposals[sid]=item
    before={sid:rows[sid]['speaker'] for sid in annotated}
    after={sid:proposals.get(sid,{}).get('proposed_speaker',before[sid]) for sid in annotated}
    def score(labels):
        correct=sum(labels[sid]==truth for sid,truth in annotated.items())
        total_seconds=sum(float(rows[sid]['end'])-float(rows[sid]['start']) for sid in annotated)
        correct_seconds=sum(float(rows[sid]['end'])-float(rows[sid]['start']) for sid,truth in annotated.items() if labels[sid]==truth)
        return {'correct_segments':correct,'segment_accuracy':correct/len(annotated),
                'labeled_seconds':total_seconds,'duration_weighted_accuracy':correct_seconds/total_seconds if total_seconds else None}
    changed={sid for sid in annotated if before[sid]!=after[sid]}
    fixed=sum(before[sid]!=annotated[sid] and after[sid]==annotated[sid] for sid in changed)
    harmed=sum(before[sid]==annotated[sid] and after[sid]!=annotated[sid] for sid in changed)
    remaining_wrong=sum(before[sid]!=annotated[sid] and after[sid]!=annotated[sid] for sid in changed)
    groups={}
    for strength in ('supported','review','conflict'):
        ids={sid for sid in changed if proposals[sid].get('strength')==strength}
        groups[strength]={'changes':len(ids),'correct_after':sum(after[sid]==annotated[sid] for sid in ids),
                         'harmful_changes':sum(before[sid]==annotated[sid] and after[sid]!=annotated[sid] for sid in ids)}
    counts=Counter(row['speaker'] for row in segments)
    return {'metric':'fixed_segment_speaker_label_accuracy',
            'total_segments':len(segments),'annotated_segments':len(annotated),
            'annotation_coverage':len(annotated)/len(segments),'before':score(before),'proposed':score(after),
            'changes':{'evaluated':len(changed),'fixed':fixed,'harmful':harmed,'still_wrong':remaining_wrong,
                       'correction_precision':fixed/len(changed) if changed else None},
            'by_strength':groups,'unevaluated_proposals':sum(sid not in annotated for sid in proposals),
            'baseline_speaker_segments':dict(counts),
            'limitations':['固定片段标签比较，不是 DER，不评价漏检、重叠发言或时间边界',
                           '名称必须与基线中的发言人名称一致；人工标注应独立于模型建议',
                           'proposed 是应用全部建议的离线模拟，不代表用户实际采用结果']}
