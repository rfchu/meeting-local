import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/onboarding/readiness.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {captureState,recordingPlan}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const native={supported:true,available:true,screen_permission:true,microphone_permission:true};
test('Missing ASR degrades to recording and notes instead of blocking capture',()=>{
 assert.deepEqual(recordingPlan(false,'quick',true),{recordOnly:true,completionMode:'none',autoProcess:false});
 assert.deepEqual(recordingPlan(true,'full',true),{recordOnly:false,completionMode:'full',autoProcess:true});
 assert.deepEqual(recordingPlan(true,'none',false),{recordOnly:false,completionMode:'none',autoProcess:false});
});
test('Unknown browser permission remains pending and is not reported as denied or granted',()=>{
 assert.equal(captureState('browser',undefined,true,'unknown').state,'pending');
 assert.equal(captureState('browser',undefined,true,'denied').state,'blocked');
 assert.equal(captureState('browser',undefined,true,'granted').state,'ready');
 assert.equal(captureState('browser',undefined,false,'granted').state,'blocked');
});
test('Native system and dual-track sources request only their relevant permissions',()=>{
 assert.equal(captureState('macos-system',{...native,microphone_permission:false},false,'unknown').state,'ready');
 assert.equal(captureState('macos-both',{...native,microphone_permission:false},true,'granted').state,'pending');
 assert.equal(captureState('macos-both',native,false,'unknown').state,'ready');
 assert.equal(captureState('macos-system',{...native,available:false},true,'granted').state,'blocked');
 assert.equal(captureState('macos-system',{...native,screen_permission:false},true,'granted').state,'pending');
});
test('Native ownership blocks duplicate capture while ordinary browser recording stays independent',()=>{
 for(const lease of ['held','unknown']) {
  const value={...native,ownership:{protocol_version:1,lease,decision:'controller_unavailable',reattach_supported:false}};
  assert.equal(captureState('macos-system',value,true,'granted').state,'blocked');
  assert.equal(captureState('macos-both',value,true,'granted').state,'blocked');
  assert.equal(captureState('browser',value,true,'granted').state,'ready');
 }
 assert.equal(captureState('macos-system',{...native,ownership:{lease:'free'}},true,'granted').state,'ready');
});
