import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/recoveryInventory.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readRecoveryInventory}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const row=(id,patch={})=>({id,created:1,updated:1,duration:10,chunk_count:1,state:'interrupted',notes:[],...patch});
const health={state:'interrupted',native_running:false,audio:{saved_bytes:32},asr:{}};
const limits={localMs:10,serverMs:10,verifyMs:10};
const never=()=>new Promise(()=>{});
test('Hung browser storage is bounded and server reads start before it finishes',async()=>{
 let started=false;
 const result=await readRecoveryInventory({local:never,remote:async()=>{started=true;return [row('remote',{health})]},health:never},limits);
 assert.equal(started,true);assert.equal(result.localAvailable,false);assert.equal(result.serverAvailable,true);
 assert.equal(result.sessions[0].id,'remote');
});
test('Unavailable server preserves local material but discards cached health conclusions',async()=>{
 const result=await readRecoveryInventory({local:async()=>[row('local',{health:{state:'completed',native_running:true,recovery_archived:true}})],remote:never,health:never},limits);
 assert.equal(result.serverAvailable,false);assert.equal(result.localAvailable,true);assert.equal(result.unverifiedCount,1);
 assert.equal(result.sessions.length,1);assert.equal(result.sessions[0].health,undefined);
});
test('A fresh server publication check removes stale local entries without deleting them',async()=>{
 const local=[row('published')];
 const result=await readRecoveryInventory({local:async()=>local,remote:async()=>[],health:async()=>({...health,state:'completed'})},limits);
 assert.equal(result.sessions.length,0);assert.equal(result.unverifiedCount,0);assert.equal(local.length,1);assert.equal(local[0].state,'interrupted');
});
test('Verification concurrency is limited and shared deadline stops queued probes',async()=>{
 let count=0,resolveLate;const signals=[];
 const result=await readRecoveryInventory({local:async()=>Array.from({length:30},(_,i)=>row(String(i))),remote:async()=>[],health:(_id,signal)=>{count++;signals.push(signal);return new Promise(resolve=>{resolveLate=resolve})}},limits);
 assert.equal(count,4);assert.equal(result.unverifiedCount,30);assert.ok(signals.every(s=>s.aborted));
 resolveLate({...health,state:'completed'});await new Promise(setImmediate);
 assert.equal(count,4);assert.equal(result.sessions.length,30);assert.equal(result.sessions[3].health,undefined);
});
test('Unknown local records never override authoritative server capture state or unsynced notes',async()=>{
 let probes=0;
 const result=await readRecoveryInventory({local:async()=>[row('same',{updated:10,notes:[{raw_text:'local note'}],health:{state:'completed'}})],remote:async()=>[row('same',{health:{...health,native_running:true,state:'recording'}})],health:async()=>{probes++;return health}},limits);
 assert.equal(probes,0);assert.equal(result.sessions[0].health.native_running,true);assert.equal(result.sessions[0].notes[0].raw_text,'local note');
});
test('Failed probes and both unavailable sources stay explicitly unconfirmed',async()=>{
 const result=await readRecoveryInventory({local:async()=>[row('unknown')],remote:async()=>[],health:async()=>{throw Error('private failure')}},limits);
 assert.equal(result.unverifiedCount,1);assert.equal(result.sessions.length,1);
 const empty=await readRecoveryInventory({local:never,remote:never,health:never},limits);
 assert.equal(empty.localAvailable,false);assert.equal(empty.serverAvailable,false);assert.deepEqual(empty.sessions,[]);
});
test('A recovered source can be checked again and known active state remains authoritative',async()=>{
 let remoteCalls=0;
 const io={local:async()=>[row('same')],remote:async()=>{if(++remoteCalls===1)throw Error('offline');return [row('same',{health:{...health,native_running:true}})]},health:never};
 const failed=await readRecoveryInventory(io,limits);assert.equal(failed.serverAvailable,false);
 const restored=await readRecoveryInventory(io,limits);assert.equal(restored.serverAvailable,true);assert.equal(restored.sessions[0].health.native_running,true);
});
test('Repeated inventory checks share one hung browser read instead of opening more database requests',async(t)=>{
 const bufferBuild=await build({entryPoints:[new URL('../src/features/notes/recordingBuffer.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
 const {recoveryInventorySnapshot}=await import('data:text/javascript;base64,'+Buffer.from(bufferBuild.outputFiles[0].text).toString('base64'));
 const previousFetch=globalThis.fetch,previousIDB=globalThis.indexedDB;let opens=0;
 globalThis.indexedDB={open:()=>{opens++;return {}}};
 globalThis.fetch=async()=>new Response(JSON.stringify([row('remote')]));
 t.mock.timers.enable({apis:['setTimeout']});
 try {
  const first=recoveryInventorySnapshot(),second=recoveryInventorySnapshot();
  await new Promise(setImmediate);t.mock.timers.tick(2501);
  const results=await Promise.all([first,second]);
  assert.equal(opens,1);assert.ok(results.every(r=>r.serverAvailable&&!r.localAvailable&&r.sessions[0].id==='remote'));
 } finally{t.mock.timers.reset();globalThis.fetch=previousFetch;if(previousIDB===undefined)delete globalThis.indexedDB;else globalThis.indexedDB=previousIDB;}
});
