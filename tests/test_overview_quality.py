import json
from pathlib import Path
from app.overview_quality import same_item,select_items
from app.overview_evaluation import evaluate_cases

def item(text,kind='point',sid='source',key='item'):
    return {'id':key,'kind':kind,'content':text,'evidence':[{'segment_id':sid,'quote':text}]}

def test_deduplication_preserves_numbers_negation_and_unrelated_sources():
    assert same_item(item('预算1.5万元。'),item('预算1.5万元'))
    for other in [item('预算15万元。'),item('预算1.5万元未批准。'),item('预算1.5万元。',sid='another')]:
        assert not same_item(item('预算1.5万元。'),other)

def test_long_meeting_bounds_pending_but_never_discards_human_confirmations():
    pinned=[item('人工确认'+str(n),key=str(n)) for n in range(12)]
    incoming=[item('新观点'+str(n),key='new'+str(n)) for n in range(8)]
    carried=[item('待确认问题','question',key='question'),item('待办','action',key='action')]
    selected,quality=select_items(incoming,carried,pinned)
    assert selected[:12]==pinned and len(selected)<=20
    assert sum(i['kind']=='point' for i in selected[12:])==4
    assert any(i['id']=='question' for i in selected) and any(i['id']=='action' for i in selected)
    assert quality['overflow_count']==4

def test_fixed_incremental_gold_cases(tmp_path):
    cases=json.loads((Path(__file__).parent/'fixtures/overview_cases.json').read_text())
    report=evaluate_cases(cases,tmp_path)
    assert report['model_called'] is False
    assert report['passed'],json.dumps(report,ensure_ascii=False)

def test_explicit_model_mode_uses_generation_and_reports_calls(tmp_path,monkeypatch):
    from app.live_overview import LiveOverview
    calls=[]
    def generate(self,plan):
        calls.append(plan['config']['model'])
        source=plan['input']['new_segments'][0]
        return {'items':[{'id':None,'kind':'decision','content':source['text'],'evidence':[{'segment_id':source['id'],'quote':source['text']}],'note_ids':[]}]}
    monkeypatch.setattr(LiveOverview,'generate',generate)
    cases=[{'id':'model-branch','rounds':[{'segments':[{'id':'s1','text':'大家明确同意周五交付。'}],'items':[],'required':['周五交付']}]}]
    report=evaluate_cases(cases,tmp_path,model='test-local')
    assert calls==['test-local'] and report['model_called'] and report['model_calls']==1
    assert report['passed'] and report['rounds'][0]['generation']=='local_model'
