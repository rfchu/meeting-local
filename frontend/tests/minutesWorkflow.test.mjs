import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/utils.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {latestMinutes,currentMinutes}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const composition=await build({entryPoints:[new URL('../src/features/minutes/composition.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readMinutesOptions,persistMinutesOptions}=await import('data:text/javascript;base64,'+Buffer.from(composition.outputFiles[0].text).toString('base64'));
const presentation=await build({entryPoints:[new URL('../src/features/minutes/presentation.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {resolveMinutesStaleNotice,resolveAnalysisStaleNotice}=await import('data:text/javascript;base64,'+Buffer.from(presentation.outputFiles[0].text).toString('base64'));
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
test('stale notice consolidates revision lag and source drift into one compact banner with regenerate CTA',()=>{
  const lag=resolveMinutesStaleNotice({
    artifact:{revision:1,stale:true},
    meetingRevision:2,
    transcriptDirty:false,
    readOnly:true,
    sameRevisionRecordChanged:false,
    analysisUpdated:false,
  });
  assert.ok(lag);
  assert.match(lag.message,/v1.*v2/);
  assert.equal(lag.actionLabel,'用 v2 重新生成');

  const dirty=resolveMinutesStaleNotice({
    artifact:{revision:2,stale:false},
    meetingRevision:2,
    transcriptDirty:true,
    readOnly:false,
  });
  assert.ok(dirty);
  assert.match(dirty.message,/正在修改/);
  assert.equal(dirty.actionLabel,'用 v2 重新生成');

  const sameRev=resolveMinutesStaleNotice({
    artifact:{revision:2,stale:true},
    meetingRevision:2,
    transcriptDirty:false,
    readOnly:true,
    sameRevisionRecordChanged:true,
  });
  assert.ok(sameRev);
  assert.match(sameRev.message,/中文记录/);

  assert.equal(resolveMinutesStaleNotice({
    artifact:{revision:2,stale:false},
    meetingRevision:2,
    transcriptDirty:false,
    readOnly:false,
  }),null);

  const analysisLag=resolveAnalysisStaleNotice({
    artifact:{revision:1},
    meetingRevision:3,
    transcriptDirty:false,
  });
  assert.ok(analysisLag);
  assert.equal(analysisLag.actionLabel,'用 v3 重新生成');
  assert.equal(resolveAnalysisStaleNotice({
    artifact:{revision:3},
    meetingRevision:3,
    transcriptDirty:false,
  }),null);
});
