"""Offline gold-case checks. No scores here imply a real model has been benchmarked."""
import time
from .db import Store,uid
from .recordings import RecordingStore
from .live_results import LiveResults
from .live_overview import LiveOverview,EnableInput,ReviewInput
from .overview_quality import same_item


def evaluate_cases(cases,root,model=None,base_url='http://127.0.0.1:1234/v1'):
    store=Store(root);recordings=RecordingStore(store);cache=LiveResults(store,recordings)
    service=LiveOverview(store,recordings,cache)
    pid=uid();store.execute('INSERT INTO llm_configs(id,name,provider_type,base_url,model) VALUES(?,?,?,?,?)',(pid,'overview-evaluation','local',base_url,model or 'offline-fixture'))
    results=[];model_calls=0
    for case in cases:
        session=recordings.create('audio/webm');rid=session['id'];sources={};offset=0
        service.enable(rid,EnableInput(profile_id=pid))
        for index,turn in enumerate(case['rounds']):
            for part in turn.get('segments',[]):
                aid,_=cache.begin(rid,'browser','qwen',offset,30,part['text'].encode(),{})
                cache.complete(aid,[{'text':part['text'],'start':offset,'end':offset+30}]);offset+=30
                sources[part['id']]={'segment_id':aid+':0','quote':part['text']}
            recordings.metadata(rid,[],offset,0)
            before=service.read(rid);candidates=[];stable_ids=[]
            for candidate in turn['items']:
                old=next((i for i in before['items'] if candidate.get('reuse') and candidate['reuse'] in i['content']),None)
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
                'no_forbidden_claims':all(value not in text for value in turn.get('forbidden',[])),
                'no_duplicate_items':not any(same_item(a,b) for n,a in enumerate(items) for b in items[n+1:]),
                'stable_ids':all(value in {i['id'] for i in items} for value in stable_ids),
                'failure_preserves_version':not failed or before['version']==current['version'],
                'expected_count':'expected_count' not in turn or len(items)==turn['expected_count'],
                'pinned_preserved':all(any(i['id']==old['id'] and i['content']==old['content'] for i in items) for old in before['items'] if old['decision']=='confirmed'),
            }
            results.append({'case':case['id'],'round':index+1,'generation':'local_model' if use_model else 'fixture',
                            'elapsed_seconds':round(time.monotonic()-started,3),'checks':checks,'passed':all(checks.values())})
            for review in turn.get('reviews',[]):
                item=next((i for i in current['items'] if review['contains'] in i['content']),None)
                if item:service.decide(rid,item['id'],ReviewInput(state=review['state'],fingerprint=item['fingerprint'],version=item['review_version']))
    return {'mode':'local_model_with_failure_probe' if model else 'offline_pipeline_fixtures',
            'model_called':bool(model_calls),'model_calls':model_calls,
            'passed':all(r['passed'] for r in results),'rounds':results}
