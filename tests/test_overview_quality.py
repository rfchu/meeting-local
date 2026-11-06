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
        assert plan['profile']['api_key']=='private-eval-token'
        source=plan['input']['new_segments'][0]
        return {'items':[{'id':None,'kind':'decision','content':source['text'],'evidence':[{'segment_id':source['id'],'quote':source['text']}],'note_ids':[]}]}
    monkeypatch.setattr(LiveOverview,'generate',generate)
    cases=[{'id':'model-branch','rounds':[{'segments':[{'id':'s1','text':'大家明确同意周五交付。'}],'items':[],'required':['周五交付']}]}]
    report=evaluate_cases(cases,tmp_path,model='test-local',api_key='private-eval-token')
    assert calls==['test-local'] and report['model_called'] and report['model_calls']==1
    assert report['passed'] and report['rounds'][0]['generation']=='local_model'
    assert 'private-eval-token' not in json.dumps(report)

def test_fact_rules_allow_paraphrase_but_reject_unconfirmed_date_promotion():
    from app.overview_evaluation import matches_rule
    rule={'kinds':['action'],'all':['张敏','测试清单']}
    assert matches_rule({'kind':'action','content':'张敏负责整理测试清单。'},rule)
    assert not matches_rule({'kind':'question','content':'张敏是否负责测试清单？'},rule)
    forbidden={'kinds':['decision'],'all':['上线','下周一','调整'],'none':['建议','提议','未定']}
    assert matches_rule({'kind':'decision','content':'上线日期由下周一调整为下周三。'},forbidden)
    assert not matches_rule({'kind':'decision','content':'原建议下周一上线，现确定下周三上线。'},forbidden)

def test_source_bound_reviews_work_after_model_paraphrase(tmp_path,monkeypatch):
    from app.live_overview import LiveOverview
    def generate(self,plan):
        source=plan['input']['new_segments'][0]
        return {'items':[{'id':None,'kind':'action','content':'张敏将负责整理测试清单。','evidence':[{'segment_id':source['id'],'quote':source['text']}],'note_ids':[]}]}
    monkeypatch.setattr(LiveOverview,'generate',generate)
    cases=[{'id':'reviews','rounds':[{'segments':[{'id':'s1','text':'张敏整理测试清单。'}],'items':[], 'reviews':[{'source':'s1','kind':'action','state':'confirmed'}]}]}]
    report=evaluate_cases(cases,tmp_path,model='stub')
    assert report['passed']

def test_rescoring_allows_only_fact_rule_changes_and_preserves_real_timings(tmp_path):
    import copy,pytest
    from app.overview_evaluation import rescore_report,matches_rule
    old=[{'id':'pending','rounds':[{'segments':[{'id':'s1','text':'日期尚未最终确定。'}], 'items':[{'kind':'question','content':'日期尚未最终确定。','source':'s1'}], 'required':['日期尚未确定']}]}]
    report=evaluate_cases(old,tmp_path);assert not report['passed']
    new=copy.deepcopy(old);row=new[0]['rounds'][0];row.pop('required');row['required_rules']=[{'kinds':['question'],'all':['日期'],'patterns_any':['未.{0,4}确定']}]
    revised=rescore_report(report,new,old)
    assert revised['passed'] and revised['rounds'][0]['elapsed_seconds']==report['rounds'][0]['elapsed_seconds']
    assert revised['rescored_from_case_hash']==report['case_hash']
    assert not matches_rule({'kind':'question','content':'日期已经最终确定。'},row['required_rules'][0])
    changed=copy.deepcopy(new);changed[0]['rounds'][0]['segments'][0]['text']='日期已经确定。'
    with pytest.raises(ValueError,match='不一致'):rescore_report(report,changed,old)
