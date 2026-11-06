import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/nativeCaptureClock.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {nativeCaptureSeconds}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Native timer stops extrapolating when capture is stale, paused or unreachable',()=>{
 assert.equal(nativeCaptureSeconds(649,1000,2000,true),650);
 assert.equal(nativeCaptureSeconds(649,1000,181000,true),652);
 assert.equal(nativeCaptureSeconds(649,1000,181000,false),649);
 assert.equal(nativeCaptureSeconds(649,2000,1000,true),649);
});
