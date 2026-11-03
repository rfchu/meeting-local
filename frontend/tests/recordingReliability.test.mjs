import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const compiled=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));}
const {mergeRecordingInventory,clientRecordingDiagnostics,emptyRecording,recordingRecoveryCount}=await load('../src/features/notes/recordingInventory.ts');
const {stopRecorder}=await load('../src/features/notes/stopRecorder.ts');
const {recoverableRecordings}=await load('../src/features/notes/recordingBuffer.ts');
const fixture={id:'recording',created:1,updated:10,duration:15,chunk_count:3,state:'recording',notes:[]};

test('Merge retains unsynced tail and notes without losing authoritative native activity',()=>{
  const local={...fixture,updated:20,duration:20,chunk_count:4,notes:[{raw_text:'local tail note'}]};
  const remote={...fixture,saved_chunks:2,health:{state:'recording',native_running:true}};
  const [merged]=mergeRecordingInventory([local],[remote]);
  assert.equal(merged.chunk_count,4);assert.equal(merged.duration,20);assert.equal(merged.notes[0].raw_text,'local tail note');
  assert.equal(merged.health.native_running,true);assert.equal(merged.saved_chunks,2);
});
test('Completed stale browser entries stay hidden; note-only interruptions remain discoverable',()=>{
  assert.deepEqual(mergeRecordingInventory([{...fixture,health:{state:'completed'}}],[]),[]);
  assert.deepEqual(mergeRecordingInventory([{...fixture,state:'completed'}],[]),[]);
  const [notes]=mergeRecordingInventory([{...fixture,chunk_count:0,notes:[{raw_text:'only note'}]}],[]);
  assert.equal(notes.notes.length,1);
});
test('Disabled IndexedDB cannot hide server recordings',async()=>{
  const originalFetch=globalThis.fetch,originalIDB=globalThis.indexedDB;
  globalThis.indexedDB={open:()=>{throw Error('disabled');}};
  globalThis.fetch=async()=>new Response(JSON.stringify([{...fixture,saved_chunks:3}]));
  try{const sessions=await recoverableRecordings();assert.equal(sessions.length,1);assert.equal(sessions[0].id,'recording');}
  finally{globalThis.fetch=originalFetch;if(originalIDB===undefined)delete globalThis.indexedDB;else globalThis.indexedDB=originalIDB;}
});
test('Client diagnostics exclude arbitrary strings, identity, content and raw status',()=>{
  const secret='PRIVATE-SENTINEL';
  const report=clientRecordingDiagnostics(secret,2,1,{firstMs:100,processingMs:200,rtf:Infinity,lagMs:NaN,revisions:3,text:secret,api_key:secret});
  assert.equal(JSON.stringify(report).includes(secret),false);assert.equal(report.state,'unknown');
  assert.equal(report.browser_pending_chunks,2);assert.equal(report.memory_only_chunks,1);
  assert.equal(report.metrics.rtf,null);assert.equal(report.metrics.confirmation_lag_ms,null);
});
test('Stopping waits for final data; an automatically stopped recorder never waits for a second stop event',async()=>{
  class Recorder extends EventTarget {
    state='recording';calls=0;
    stop(){this.calls++;this.state='inactive';queueMicrotask(()=>{this.dispatchEvent(new Event('dataavailable'));this.dispatchEvent(new Event('stop'));});}
  }
  const recorder=new Recorder(),events=[];
  recorder.addEventListener('dataavailable',()=>events.push('tail'));
  await stopRecorder(recorder);events.push('saved');
  assert.deepEqual(events,['tail','saved']);assert.equal(recorder.calls,1);
  await stopRecorder(recorder);assert.equal(recorder.calls,1);
  const alreadyStopped=new Recorder();alreadyStopped.state='inactive';await stopRecorder(alreadyStopped);
  assert.equal(alreadyStopped.calls,0);
});

test('Failed empty native starts do not count as saved recordings; active capture stays separate',()=>{
  const empty={...fixture,source:'macos',chunk_count:0,duration:0,health:{state:'failed',native_running:false,audio:{saved_bytes:0},asr:{}}};
  assert.equal(emptyRecording(empty),true);
  assert.equal(recordingRecoveryCount([empty]),0);
  assert.equal(emptyRecording({...empty,health:undefined}),false);
  const active={...empty,health:{...empty.health,state:'recording',native_running:true}};
  assert.equal(emptyRecording(active),false);
  assert.equal(recordingRecoveryCount([active]),0);
  for(const patch of [{notes:[{raw_text:'local unsynced note'}]},{chunk_count:1},{native_audio_saved:true},{brief:{purpose:'meeting preparation'}}]){
    const retained={...empty,...patch};assert.equal(emptyRecording(retained),false);assert.equal(recordingRecoveryCount([retained]),1);
  }
  assert.equal(emptyRecording({...empty,health:{...empty.health,empty_session:false}}),false);
});
test('Archived empty records remain available to restore but never create a recovery banner',()=>{
  const empty={...fixture,source:'macos',chunk_count:0,health:{state:'failed',native_running:false,empty_session:true,recovery_archived:true,audio:{saved_bytes:0},asr:{}}};
  const rows=mergeRecordingInventory([], [empty]);assert.equal(rows.length,1);
  assert.equal(recordingRecoveryCount(rows),0);
});
