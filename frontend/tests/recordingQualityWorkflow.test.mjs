import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const c=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(c.outputFiles[0].text).toString('base64'));}
const {recordingQualityAttention}=await load('../src/features/notes/recordingQuality.ts');
const base={recorded:true,available:true,pending_count:0,gap_count:0,reviewed_count:1};
test('Cleared observations and imported recordings do not generate repeated workflow reminders',()=>{
 assert.equal(recordingQualityAttention(base),null);
 assert.equal(recordingQualityAttention({...base,recorded:false}),null);
 assert.equal(recordingQualityAttention(undefined),null);
});
test('Pending reviews retain confirmed gap context, and a gap stays visible after review completion',()=>{
 const pending=recordingQualityAttention({...base,pending_count:2,gap_count:1});
 assert.equal(pending.kind,'pending');assert.match(pending.text,/2 条/);assert.match(pending.text,/1 条已确认缺音/);
 assert.equal(recordingQualityAttention({...base,gap_count:1}).kind,'gap');
});
test('Unavailable diagnostics cannot be presented as no recording problems',()=>{
 assert.equal(recordingQualityAttention({...base,available:false}).kind,'unknown');
});
