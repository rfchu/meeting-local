"""Offline gold-case checks. No scores here imply a real model has been benchmarked."""
import time
import hashlib,json,re,statistics,copy
from .db import Store,uid
from .recordings import RecordingStore
from .live_results import LiveResults
from .live_overview import LiveOverview,EnableInput,ReviewInput
from .overview_quality import same_item
from .overview_prompt import PROMPT_VERSION
EVALUATION_VERSION=3


def summarize(results):
    actual=[r for r in results if r['generation']=='local_model']
    warm=[r['elapsed_seconds'] for r in actual[1:]]
    return {'real_rounds':len(actual),'real_rounds_passed':sum(r['passed'] for r in actual),
            'failure_probe_rounds':sum(r['generation']=='fixture' for r in results),
            'first_request_seconds':actual[0]['elapsed_seconds'] if actual else None,
            'subsequent_median_seconds':round(statistics.median(warm),3) if warm else None,
            'failed_checks':sorted({key for r in results for key,passed in r['checks'].items() if not passed})}


def matches_rule(item,rule):
    return (not rule.get('kinds') or item['kind'] in rule['kinds']) and all(t in item['content'] for t in rule.get('all',[])) and (not rule.get('any') or any(t in item['content'] for t in rule['any'])) and not any(t in item['content'] for t in rule.get('none',[])) and (not rule.get('patterns_any') or any(re.search(pattern,item['content']) for pattern in rule['patterns_any']))


def case_hash(cases):
    return hashlib.sha256(json.dumps(cases,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def rescore_report(report,cases,previous_cases):
    """Recheck recorded facts only when source rounds/review operations are identical."""
    def inputs(values):
        return [{'id':c['id'],'rounds':[{k:v for k,v in row.items() if k in ('segments','items','reviews','expect_failure')} for row in c['rounds']]} for c in values]
    if case_hash(previous_cases)!=report['case_hash'] or inputs(cases)!=inputs(previous_cases):raise ValueError('原样例指纹或输入／核对操作不一致，必须重新调用模型评测')
    revised=copy.deepcopy(report);turns={(c['id'],i+1):r for c in cases for i,r in enumerate(c['rounds'])}
    for row in revised['rounds']:
        turn=turns[(row['case'],row['round'])];items=row['observed_items'];text='\n'.join(i['content'] for i in items)
        row['checks'].update(required_facts=all(value in text for value in turn.get('required',[])),
            required_fact_rules=all(any(matches_rule(i,rule) for i in items) for rule in turn.get('required_rules',[])),
            no_forbidden_claims=all(value not in text for value in turn.get('forbidden',[])),
            no_forbidden_rules=not any(matches_rule(i,rule) for rule in turn.get('forbidden_rules',[]) for i in items))
        row['passed']=all(row['checks'].values())
    revised.update(rescored_from_case_hash=report['case_hash'],rescored_from_evaluation_version=report.get('evaluation_version'),evaluation_version=EVALUATION_VERSION,case_hash=case_hash(cases),summary=summarize(revised['rounds']),passed=all(r['passed'] for r in revised['rounds']))
    return revised


def source_item(items,source,kind):
    matched=[item for item in items if item['kind']==kind and any(e['segment_id']==source for e in item['evidence'])]
    return matched[0] if len(matched)==1 else None


def evaluate_cases(cases,root,model=None,base_url='http://127.0.0.1:1234/v1',api_key='',on_round=None):
    store=Store(root);recordings=RecordingStore(store);cache=LiveResults(store,recordings)
    service=LiveOverview(store,recordings,cache)
    pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model,api_key) VALUES(?,?,?,?,?,?)',(pid,'overview-evaluation','local',base_url,model or 'offline-fixture',api_key if model else ''))
    results=[];model_calls=0
    for case in cases:
        session=recordings.create('audio/webm');rid=session['id'];sources={};offset=0;anchors={}
        service.enable(rid,EnableInput(profile_id=pid))
        for index,turn in enumerate(case['rounds']):
            for part in turn.get('segments',[]):
                aid,_=cache.begin(rid,'browser','qwen',offset,30,part['text'].encode(),{})
                cache.complete(aid,[{'text':part['text'],'start':offset,'end':offset+30}]);offset+=30
                sources[part['id']]={'segment_id':aid+':0','quote':part['text']}
            recordings.metadata(rid,[],offset,0)
            before=service.read(rid);candidates=[];stable_ids=[]
            for candidate in turn['items']:
                old=next((i for i in before['items'] if candidate.get('reuse') and i['id']==anchors.get(candidate['reuse'])),None)
                evidence=dict(sources[candidate['source']])
                if 'quote' in candidate:evidence['quote']=candidate['quote']
                candidates.append({'id':old['id'] if old else None,'kind':candidate['kind'],'content':candidate['content'],'evidence':[evidence],'note_ids':[]})
                if old:stable_ids.append(old['id'])
            plan=service.prepare(rid,store.one('SELECT * FROM live_overview_runs WHERE recording_id=?',(rid,)),True)
            failed=False;started=time.monotonic()
            use_model=bool(model and not turn.get('expect_failure'))
            try:
                if use_model:model_calls+=1
                result=service.generate(plan) if use_model else {'items':candidates}
                service.publish(plan,result)
            except ValueError:failed=True
            current=service.read(rid);items=[i for i in current['items'] if i['decision']!='dismissed'];text='\n'.join(i['content'] for i in items)
            checks={
                'expected_failure':failed==bool(turn.get('expect_failure')),
                'required_facts':all(value in text for value in turn.get('required',[])),
                'required_fact_rules':all(any(matches_rule(i,rule) for i in items) for rule in turn.get('required_rules',[])),
                'no_forbidden_claims':all(value not in text for value in turn.get('forbidden',[])),
                'no_forbidden_rules':not any(matches_rule(i,rule) for rule in turn.get('forbidden_rules',[]) for i in items),
                'no_duplicate_items':not any(same_item(a,b) for n,a in enumerate(items) for b in items[n+1:]),
                'stable_ids':all(value in {i['id'] for i in items} for value in stable_ids),
                'stable_anchor_found':len(stable_ids)==sum(bool(candidate.get('reuse')) for candidate in turn['items']),
                'failure_preserves_version':not failed or before['version']==current['version'],
                'expected_count':'expected_count' not in turn or len(items)==turn['expected_count'],
                'pinned_preserved':all(any(i['id']==old['id'] and i['content']==old['content'] for i in items) for old in before['items'] if old['decision']=='confirmed'),
                'dismissed_stays_hidden':not any(i['id']==old['id'] for i in items for old in before['items'] if old['decision']=='dismissed'),
                'no_meta_narration':not re.search(r'(?:证据|evidence)\s*[eE]\d+|属于.{0,8}(?:行动项|后续行动)',text),
            }
            results.append({'case':case['id'],'round':index+1,'generation':'local_model' if use_model else 'fixture',
                            'elapsed_seconds':round(time.monotonic()-started,3),'checks':checks,'passed':all(checks.values()),
                            'item_count':len(items),'content_characters':sum(len(i['content']) for i in items),
                            'observed_items':[{'kind':i['kind'],'content':i['content'],'quotes':[e['quote'] for e in i['evidence']]} for i in items]})
            for candidate in turn['items']:
                match=source_item(items,sources[candidate['source']]['segment_id'],candidate['kind'])
                if match:anchors[candidate['content'].rstrip('。')]=match['id']
            for review in turn.get('reviews',[]):
                item=source_item(current['items'],sources[review['source']]['segment_id'],review['kind']) if review.get('source') else next((i for i in current['items'] if review['contains'] in i['content']),None)
                if item:service.decide(rid,item['id'],ReviewInput(state=review['state'],fingerprint=item['fingerprint'],version=item['review_version']))
                else:results[-1]['checks']['review_target_found']=False;results[-1]['passed']=False
            if on_round:on_round(results[-1])
    return {'mode':'local_model_with_failure_probe' if model else 'offline_pipeline_fixtures',
            'model_called':bool(model_calls),'model_calls':model_calls,
            'model':model,'prompt_version':PROMPT_VERSION,'case_hash':case_hash(cases),
            'evaluation_version':EVALUATION_VERSION,'summary':summarize(results),
            'passed':all(r['passed'] for r in results),'rounds':results}
