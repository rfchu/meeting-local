import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {QueryClient,QueryObserver} from '@tanstack/react-query';
const compiled=await build({entryPoints:[new URL('../src/features/notes/useRecordingHealth.ts',import.meta.url).pathname,new URL('../src/features/notes/healthFreshness.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false,outdir:'/tmp/unused'});
const modules=await Promise.all(compiled.outputFiles.map(file=>import('data:text/javascript;base64,'+Buffer.from(file.text).toString('base64'))));
const {healthQueryOptions}=modules.find(m=>m.healthQueryOptions);
const {healthFreshness}=modules.find(m=>m.healthFreshness);
test('Initial, expired and rolled-back local receipt times never show current capture',()=>{
 assert.equal(healthFreshness(0,1000,false),'checking');
 assert.equal(healthFreshness(1000,21000,false),'current');
 assert.equal(healthFreshness(1000,21001,false),'stale');
 assert.equal(healthFreshness(1000,999,false),'stale');
 assert.equal(healthFreshness(NaN,1000,false),'checking');
 assert.equal(healthFreshness(1000,Infinity,false),'stale');
});
test('Return recheck and connection failure override even a recent cached snapshot',()=>{
 assert.equal(healthFreshness(1000,1001,false,true),'checking');
 assert.equal(healthFreshness(1000,1001,true),'unavailable');
 assert.equal(healthFreshness(1000,1001,false),'current');
});
test('A blocked browser database does not block server health updates',async()=>{
 const client=new QueryClient();let serverCalls=0;
 const options=healthQueryOptions('browser',false,async()=>({runtime:{phase:++serverCalls===1?'recording':'interrupted'}}),()=>new Promise(()=>{}));
 const local=new QueryObserver(client,options.local);const unwatch=local.subscribe(()=>{});
 try {
  const first=await client.fetchQuery(options.server);assert.equal(first.runtime.phase,'recording');
  const next=await client.fetchQuery(options.server);assert.equal(next.runtime.phase,'interrupted');
  assert.equal(local.getCurrentResult().fetchStatus,'fetching');
 } finally {unwatch();client.clear();}
});
test('Native capture health does not open the browser audio database',async()=>{
 const client=new QueryClient();let localCalls=0;
 const options=healthQueryOptions('native',true,async()=>({}),async()=>{localCalls++;return 0});
 const local=new QueryObserver(client,options.local);const unwatch=local.subscribe(()=>{});
 try {assert.equal(localCalls,0);assert.equal(local.getCurrentResult().fetchStatus,'idle');}
 finally{unwatch();client.clear();}
});
test('Superseded health requests are aborted and late responses cannot overwrite new state',async()=>{
 const client=new QueryClient();let oldSignal,resolveOld;
 const old=healthQueryOptions('same',true,(_id,signal)=>{oldSignal=signal;return new Promise(resolve=>resolveOld=resolve)}).server;
 const first=client.fetchQuery(old).catch(()=>null);
 await client.cancelQueries({queryKey:old.queryKey});assert.equal(oldSignal.aborted,true);
 const fresh=healthQueryOptions('same',true,async()=>({runtime:{phase:'interrupted'}})).server;
 await client.fetchQuery(fresh);resolveOld({runtime:{phase:'recording'}});await first;
 assert.equal(client.getQueryData(old.queryKey).runtime.phase,'interrupted');client.clear();
});
test('Health HTTP request combines caller cancellation with its existing timeout',async()=>{
 const compiledApi=await build({entryPoints:[new URL('../src/api.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
 const {api}=await import('data:text/javascript;base64,'+Buffer.from(compiledApi.outputFiles[0].text).toString('base64'));
 const previous=globalThis.fetch;const controller=new AbortController();let observed;
 globalThis.fetch=(_path,init)=>new Promise((_resolve,reject)=>{
  observed=init.signal;init.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});
 });
 try {
  const result=api.recordingHealth('synthetic',controller.signal).catch(error=>error.message);
  controller.abort();assert.equal(await result,'aborted');assert.equal(observed.aborted,true);
 } finally {globalThis.fetch=previous;}
});
