import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/utils.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {latestMinutes,currentMinutes}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const composition=await build({entryPoints:[new URL('../src/features/minutes/composition.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readMinutesOptions,persistMinutesOptions}=await import('data:text/javascript;base64,'+Buffer.from(composition.outputFiles[0].text).toString('base64'));
test('new draft never silently replaces adopted minutes; stale adopted version remains viewable',()=>{
  const adopted={id:'adopted',kind:'meeting_minutes',revision:1,stale:false};
  const draft={id:'draft',kind:'meeting_minutes',revision:1,stale:false};
  const meeting={revision:1,active_minutes_id:'adopted',artifacts:[draft,adopted]};
  assert.equal(latestMinutes(meeting).id,'adopted');
  assert.equal(currentMinutes(meeting).id,'adopted');
  meeting.revision=2;
  assert.equal(latestMinutes(meeting).id,'adopted');
  assert.equal(currentMinutes(meeting),undefined);
  meeting.revision=1;adopted.stale=true;
  assert.equal(currentMinutes(meeting),undefined);
});
test('legacy meetings without an explicit pointer still open their last minutes',()=>{
  const artifact={id:'legacy',kind:'meeting_minutes',revision:1,stale:false};
  assert.equal(latestMinutes({revision:1,artifacts:[artifact]}).id,'legacy');
});
test('generation preferences persist without keeping an obsolete derivation source',()=>{
  const data=new Map();globalThis.localStorage={getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)};
  persistMinutesOptions({template_id:'project-weekly',detail_level:'brief',audience:'internal',method:'current'});
  assert.deepEqual(readMinutesOptions(),{template_id:'project-weekly',detail_level:'brief',audience:'internal',method:'ai'});
  data.set('tingji.minutes.options.v1','broken');
  assert.equal(readMinutesOptions().method,'ai');
  delete globalThis.localStorage;
});
