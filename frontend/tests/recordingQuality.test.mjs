import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/recordingQuality.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {qualityPosition,qualitySeek,qualityResolution}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const item={start_seconds:20,end_seconds:30,closed_at:100,end_state:'receiving'};
test('Audio review starts before the observation and never before zero',()=>{
 assert.equal(qualitySeek(item),10);assert.equal(qualitySeek({...item,start_seconds:5}),0);
 assert.match(qualityPosition(item),/观测位置 00:20–00:30/);
});
test('Unknown timestamps have no invented replay location',()=>{
 const unknown={...item,start_seconds:null};assert.equal(qualitySeek(unknown),null);assert.equal(qualityPosition(unknown),'音频位置未记录');
});
test('Capture stopping or pausing is not described as restored audio',()=>{
 assert.match(qualityResolution({...item,end_state:'stopped'}),/未确认/);
 assert.match(qualityResolution({...item,end_state:'paused'}),/未确认/);
 assert.match(qualityResolution({...item,closed_at:null}),/尚未观测到恢复/);
 assert.match(qualityResolution(item),/回听确认/);
});
