import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/components/reviewProgress.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {reviewProgress}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const keys=['transcript','speakers','notes','minutes'];
function fixture(){return {id:'meeting',title:'test',revision:1,segments:[{text:'test'}],notes:[],artifacts:[{id:'minutes-1',kind:'meeting_minutes',revision:1}],review:{revision:1,title:'test',minutes_id:'minutes-1',steps:Object.fromEntries(keys.map(key=>[key,{state:key==='notes'?'not_needed':'done',decision:'confirmed'}]))}};}
test('Completed quick AI minutes do not require an analysis artifact',()=>{
  const progress=reviewProgress(fixture(),false,false);
  assert.equal(progress.complete,4);assert.equal(progress.next,undefined);
});
test('Dirty transcript and revision mismatch cannot display old review as complete',()=>{
  const meeting=fixture();assert.equal(reviewProgress(meeting,true,false).complete,1);
  meeting.revision=2;assert.equal(reviewProgress(meeting,false,false).stale,3);
});
test('Unsaved or newly saved minutes invalidate only the send check immediately',()=>{
  let meeting=fixture();let progress=reviewProgress(meeting,false,true);
  assert.equal(progress.complete,3);assert.equal(progress.next.key,'minutes');assert.equal(progress.next.state,'stale');
  meeting.artifacts[0].id='minutes-2';progress=reviewProgress(meeting,false,false);
  assert.equal(progress.next.state,'stale');assert.equal(progress.complete,3);
});
test('Title changes invalidate basic information and minutes but retain speaker review',()=>{
  const meeting=fixture();meeting.title='changed';const progress=reviewProgress(meeting,false,false);
  assert.equal(progress.steps[0].state,'stale');assert.equal(progress.steps[1].state,'done');assert.equal(progress.steps[3].state,'stale');
});
test('Personal notes remain optional and do not make completed delivery look incomplete',()=>{
 const meeting=fixture();meeting.notes=[{id:'n',note_scope:'personal'}];meeting.review.steps.notes.state='pending';
 const value=reviewProgress(meeting,false,false);assert.equal(value.complete,3);assert.equal(value.requiredComplete,3);assert.equal(value.requiredTotal,3);
});
test('Unresolved meeting-intended attention remains a required review step',()=>{
 const meeting=fixture();meeting.notes=[{id:'n',note_scope:'meeting'}];meeting.review.steps.notes.state='pending';
 const value=reviewProgress(meeting,false,false);assert.equal(value.requiredComplete,3);assert.equal(value.requiredTotal,4);
});
