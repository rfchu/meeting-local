import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const result=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));}
const {currentHandoff,handoffAction,handoffRelations}=await load('../src/components/handoff.ts');
const {deliveryAttentionCount}=await load('../src/components/deliveryAttention.ts');
function fixture(){return {revision:1,title:'合成会',active_minutes_id:'a',notes:[],handoff:{version:1,revision:1,title:'合成会',primary:{code:'processing',target:'tasks',label:'查看进度',summary:'排队'},stages:[{key:'minutes',source:{artifact_id:'a'}},{key:'notes',source:{},counts:{pending_meeting:2}}]}};}
test('A waiting job directs to a usable progress destination instead of a disabled action',()=>{assert.equal(handoffAction(fixture(),false,false).target,'tasks');});
test('Unsaved local work is never replaced by server guidance',()=>{assert.equal(handoffAction(fixture(),true,false),null);assert.equal(handoffAction(fixture(),false,true),null);});
test('A different revision, title or adopted version rejects the old projection',()=>{for(const change of [{revision:2},{title:'新主题'},{active_minutes_id:'b'}])assert.equal(currentHandoff({...fixture(),...change}),null);});
test('Old servers and incomplete projections retain the compatibility workflow',()=>{assert.equal(currentHandoff({revision:1,title:'old'}),null);const value=fixture();value.handoff.stages=undefined;assert.equal(currentHandoff(value),null);});
test('Meeting attention counts use the shared projection with an old-server fallback',()=>{assert.equal(deliveryAttentionCount(fixture()),2);assert.equal(deliveryAttentionCount({notes:[{id:'n',note_scope:'meeting'}]}),1);});
test('Context, explicit adoption and confirmed tracking have different labels',()=>{assert.notEqual(handoffRelations.context,handoffRelations.explicit_adoption);assert.match(handoffRelations.confirmed_tracking,/确认/);});

const {handoffWorkspace}=await load('../src/components/handoff.ts');
test('Recording handoff chooses the relevant workspace and keeps compatibility fallback',()=>{for(const [target,expected] of [['tasks','transcript'],['transcript','transcript'],['notes','notes'],['actions','minutes'],['minutes','minutes'],['overview','notes']]){const value=fixture();value.handoff.primary.target=target;assert.equal(handoffWorkspace(value),expected);}assert.equal(handoffWorkspace({}), 'notes');});

const {handoffTaskFocus}=await load('../src/components/handoff.ts');
const {taskQuery}=await load('../src/features/management/taskScope.ts');
test('Handoff task inspection keeps both the current meeting and exact job identity',()=>{const value={...fixture(),id:'m'};value.handoff.primary.job_id='j';assert.deepEqual(handoffTaskFocus(value),{meetingId:'m',jobId:'j'});});
test('An interrupted stage can focus its own task instead of a different primary job',()=>{const value={...fixture(),id:'m'};value.handoff.primary.job_id='other';assert.deepEqual(handoffTaskFocus(value,'stage-job'),{meetingId:'m',jobId:'stage-job'});});
test('Stale source projection cannot send an old task focus to another meeting view',()=>{const value={...fixture(),id:'m',revision:2};assert.equal(handoffTaskFocus(value,'old-job'),undefined);});
test('Scoped task query encodes IDs as values and keeps completed tasks visible with all filter',()=>{const query=taskQuery('all',{meetingId:'m & other=1',jobId:'job?#'});const params=new URLSearchParams(query.split('?')[1]);assert.equal(params.get('meeting_id'),'m & other=1');assert.equal(params.get('job_id'),'job?#');assert.equal(params.get('state'),'all');assert.equal(params.size,3);});
test('Unscoped task query retains the old runtime task URL',()=>{assert.equal(taskQuery('active'),'/runtime/tasks?state=active');});
const {feedbackLabel,sourceCommandKey}=await load('../src/features/meeting/handoffSources.ts');
test('Human source feedback remains distinct from provenance validity and stale feedback is explicit',()=>{
  assert.equal(feedbackLabel({feedback:null}),'待处理');
  assert.equal(feedbackLabel({feedback:{decision:'reviewed',stale:false}}),'已人工核对');
  assert.equal(feedbackLabel({feedback:{decision:'keep_history',stale:false}}),'已标记保留历史');
  assert.equal(feedbackLabel({feedback:{decision:'reset',stale:false}}),'已撤回处理');
  assert.equal(feedbackLabel({feedback:{decision:'reviewed',stale:true}}),'来源又有变化，需重新处理');
});
test('Source command reuse is bound to artifact, item, fingerprint and decision',()=>{
  const item={source_id:'segment:s1',fingerprint:'x'};const key=sourceCommandKey('a',item,'reviewed');
  assert.equal(key,sourceCommandKey('a',item,'reviewed'));
  for(const other of [sourceCommandKey('b',item,'reviewed'),sourceCommandKey('a',{...item,fingerprint:'y'},'reviewed'),sourceCommandKey('a',item,'reset')])assert.notEqual(key,other);
});
