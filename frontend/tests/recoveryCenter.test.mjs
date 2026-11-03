import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const compiled=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));}
const {recoverSavedRecording}=await load('../src/features/notes/recoverSavedRecording.ts');
const {recoveryGroups}=await load('../src/features/notes/RecordingRecoveryList.tsx');
const {recoveryInventorySnapshot}=await load('../src/features/notes/recordingBuffer.ts');
const session={id:'fixture',created:1,updated:2,duration:12,source:'macos',notes:[{raw_text:'保留笔记'}],chunk_count:0,state:'recording'};
const health={native_running:false,state:'interrupted',audio:{saved_bytes:32000,missing_chunk_count:0},asr:{}};
function ioFixture(overrides={}){
 const calls=[];
 const io={health:async()=>{calls.push('health');return health;},buffer:async value=>{calls.push('buffer');assert.equal(value.notes[0].raw_text,'保留笔记');},flush:async()=>calls.push('flush'),finishNative:async()=>{calls.push('native');return {id:session.id,notes:session.notes};},finishBrowser:async()=>{calls.push('browser');return {id:session.id,notes:session.notes};},forget:async()=>calls.push('forget'),...overrides};
 return {io,calls};
}
test('Recovery saves material before publishing and does not start recording or inference',async()=>{
 const {io,calls}=ioFixture();const result=await recoverSavedRecording(session,io);
 assert.deepEqual(calls,['health','buffer','flush','health','native','forget']);assert.equal(result.meeting.id,session.id);assert.equal(result.cleanupPending,false);
});
test('Active capture, including a capture resumed during synchronization, blocks publication',async()=>{
 for(const activeAt of [1,2]){
  let count=0;const {io,calls}=ioFixture({health:async()=>({...health,native_running:++count===activeAt})});
  await assert.rejects(recoverSavedRecording(session,io),/录音/);assert.equal(calls.includes('native'),false);assert.equal(calls.includes('forget'),false);
 }
});
test('Missing browser chunks remain buffered and cannot be published as a complete meeting',async()=>{
 const {io,calls}=ioFixture({health:async()=>({...health,audio:{...health.audio,missing_chunk_count:1}})});
 await assert.rejects(recoverSavedRecording({...session,source:undefined,chunk_count:3},io),/分段未保存/);assert.ok(calls.includes('flush'));assert.equal(calls.includes('browser'),false);assert.equal(calls.includes('forget'),false);
});
test('Publication failure preserves recovery data; cleanup failure after publication stays a success',async()=>{
 const failed=ioFixture({finishNative:async()=>{throw Error('合成失败');}});await assert.rejects(recoverSavedRecording(session,failed.io),/合成失败/);assert.equal(failed.calls.includes('forget'),false);
 const saved=ioFixture({forget:async()=>{throw Error('暂存不可用');}});const result=await recoverSavedRecording(session,saved.io);assert.equal(result.meeting.id,session.id);assert.equal(result.cleanupPending,true);
});
test('Recovery grouping keeps empty history and active capture outside pending counts',()=>{
 const empty={...session,notes:[],health:{...health,state:'failed',audio:{saved_bytes:0},empty_session:true}};
 const archived={...empty,id:'archived',health:{...empty.health,recovery_archived:true}};
 const active={...session,id:'active',health:{...health,native_running:true}};
 const grouped=recoveryGroups([session,empty,archived,active]);assert.equal(grouped.pending.length,1);assert.equal(grouped.active.length,1);assert.equal(grouped.empty.length,1);assert.equal(grouped.archived.length,1);
});
test('Unavailable storage or server cannot be mistaken for a successfully checked empty inventory',async()=>{
 const originalFetch=globalThis.fetch,originalIDB=globalThis.indexedDB;
 globalThis.indexedDB={open:()=>{throw Error('disabled');}};globalThis.fetch=async()=>{throw Error('offline');};
 try{const snapshot=await recoveryInventorySnapshot();assert.deepEqual(snapshot.sessions,[]);assert.equal(snapshot.serverAvailable,false);assert.equal(snapshot.localAvailable,false);}
 finally{globalThis.fetch=originalFetch;if(originalIDB===undefined)delete globalThis.indexedDB;else globalThis.indexedDB=originalIDB;}
});

test('A concurrently published recording cannot clear browser notes during stale recovery',async()=>{
 for(const completedAt of [1,2]){
  let count=0;const {io,calls}=ioFixture({health:async()=>({...health,state:++count===completedAt?'completed':'interrupted'})});
  await assert.rejects(recoverSavedRecording(session,io),/其他页面保存/);assert.equal(calls.includes('forget'),false);assert.equal(calls.includes('native'),false);
 }
});
