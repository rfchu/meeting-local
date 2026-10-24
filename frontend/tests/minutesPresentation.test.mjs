import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/minutes/presentation.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {compareDocuments,canonicalMinutes,hasMinutesFollowup,pendingMinutesDrafts,minutesVersionDescription,minutesAdoptionBlockReason}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const row=(content,extra={})=>({content,source_segment_ids:['s1'],...extra});
const doc=(extra={})=>({title:'合成会议',style:'ai',template_version:2,overview:[],conclusions:[],summary:[],decisions:[],action_items:[],open_questions:[],...extra});
test('comparison detects owner, due date, status and citation changes even when text is the same',()=>{
  for (const extra of [{owner:'乙'},{due_date:'下周一'},{status:'已完成'},{source_segment_ids:['s2']}]) {
    const left=doc({action_items:[row('交付', {owner:'甲',due_date:'周五',status:'进行中'})]});
    const right=doc({action_items:[{...left.action_items[0],...extra}]});
    const diff=compareDocuments(left,right);
    assert.equal(diff.leftChanges,1);assert.equal(diff.rightChanges,1);
  }
});
test('matching is independent of order but accounts for duplicate rows',()=>{
  const diff=compareDocuments(doc({decisions:[row('A'),row('A'),row('B')]}),doc({decisions:[row('B'),row('A')]}));
  assert.equal(diff.leftChanges,1);assert.equal(diff.rightChanges,0);
  assert.equal(compareDocuments(doc({overview:[row('A',{source_segment_ids:['s1','s2']})]}),doc({overview:[row('A',{source_segment_ids:['s2','s1']})]})).leftChanges,0);
});
test('old AI fields are normalized without modifying history or reporting artificial changes',()=>{
  const legacy=doc({template_version:1,overview:[row('讨论')],conclusions:[row('总结')]});
  const next=doc({conclusions:[row('讨论')],summary:[row('总结')]});
  const diff=compareDocuments(legacy,next);
  assert.equal(diff.leftChanges+diff.rightChanges+diff.metadataChanges,0);
  assert.equal(legacy.overview.length,1);
  assert.equal(canonicalMinutes(legacy).summary[0].content,'总结');
});
test('metadata changes include people, title, meeting purpose, place and recorder',()=>{
  const diff=compareDocuments(doc(),doc({title:'新标题',attendees:'甲、乙',purpose:'确认排期',location:'会议室',recorder:'丙'}));
  assert.equal(diff.metadataChanges,5);
  assert.equal(diff.leftChanges+diff.rightChanges,0);
});
test('draft hints exclude adopted, stale, old-revision and superseded drafts',()=>{
  const artifact=(id,extra={})=>({id,kind:'meeting_minutes',revision:2,stale:false,payload:doc(),provenance:{draft:true},...extra});
  const meeting={revision:2,active_minutes_id:'active',artifacts:[artifact('active'),artifact('old',{revision:1}),artifact('stale',{stale:true}),artifact('parent'),artifact('child',{provenance:{draft:true,parent_minutes_id:'parent'}}),artifact('ready')]};
  assert.deepEqual(pendingMinutesDrafts(meeting).map(a=>a.id),['child','ready']);
});
test('damaged or historical job config cannot crash the meeting page',()=>{
  for(const value of ['broken','null','[]','1','{}','{"minutes_followup":null}']) assert.equal(hasMinutesFollowup(value),false);
  assert.equal(hasMinutesFollowup('{"minutes_followup":{"settings":{}}}'),true);
});
test('version labels distinguish manual revisions, derivations and rule compilation',()=>{
  const artifact={payload:doc({template_snapshot:{name:'项目例会'}}),provenance:{composition:{method:'current',detail_level:'brief'}}};
  assert.equal(minutesVersionDescription(artifact),'AI 派生 · 项目例会 · 精简');
  assert.match(minutesVersionDescription({...artifact,provenance:{...artifact.provenance,edited:true}}),/^人工修订/);
  assert.equal(minutesVersionDescription({...artifact,provenance:{composition:{method:'analysis',detail_level:'brief'}}}),'规则成稿 · 项目例会');
});

test('identical wording explains whether ownership, deadline, status or citations changed',()=>{
  const diff=compareDocuments(doc({action_items:[row('交付',{owner:'甲',due_date:'周五'})]}),doc({action_items:[row('交付',{owner:'乙',due_date:'周一'})]}));
  assert.equal(diff.sections.find(s=>s.key==='action_items').right[0].reason,'负责人、期限不同');
  const duplicate=compareDocuments(doc({decisions:[row('A'),row('A')]}),doc({decisions:[row('A')]}));
  assert.equal(duplicate.sections.find(s=>s.key==='decisions').left[1].reason,'重复条目数量不同');
});

test('adoption stays blocked during unsaved transcript edits or a revision mismatch even before stale polling',()=>{
  const meeting={revision:2};const artifact={revision:2,stale:false};
  assert.equal(minutesAdoptionBlockReason(meeting,artifact,false),undefined);
  assert.match(minutesAdoptionBlockReason(meeting,artifact,true),/未保存/);
  assert.match(minutesAdoptionBlockReason(meeting,{revision:1,stale:false},false),/来源已变化/);
  assert.match(minutesAdoptionBlockReason(meeting,{revision:2,stale:true},false),/来源已变化/);
});
