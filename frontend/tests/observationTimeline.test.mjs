import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/management/ObservationTimeline.tsx',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {timelineTitle}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Manual application marks stay labeled as user reports',()=>{
 assert.equal(timelineTitle({kind:'manual_marker',code:'other_app_changed'}),'用户标记：其他软件已开启／退出');
});
test('ASR flat coverage is described as an observation without asserting a model failure',()=>{
 const title=timelineTitle({kind:'progress',code:'asr_coverage_flat_with_file_growth'});
 assert.match(title,/文件增长/);assert.match(title,/暂未推进/);assert.doesNotMatch(title,/故障|崩溃|抢占/);
});
test('Unknown codes do not leak arbitrary diagnostic strings into the page',()=>{
 assert.equal(timelineTitle({kind:'observation',code:'private-device-id'}),'观测线索待核对');
 assert.match(timelineTitle({kind:'observation',code:'sampling_discontinuity'}),/期间状态待核对/);
});
