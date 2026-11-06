import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const result=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));}
const {StatusPolling}=await load('../src/features/notes/statusPolling.ts');
const {coordinateRecording}=await load('../src/features/notes/recordingCoordinator.ts');
const tick=()=>new Promise(setImmediate);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
test('A return refresh replaces in-flight observation and rejects a late old response',async()=>{
 const requests=[],applied=[],failed=[];let checking=0;
 const polling=new StatusPolling(signal=>{const d=deferred();requests.push({...d,signal});return d.promise},value=>applied.push(value),error=>failed.push(error),()=>checking++);
 const old=polling.poll();await polling.poll();assert.equal(requests.length,1);
 const next=polling.poll(true);assert.equal(requests.length,2);assert.equal(requests[0].signal.aborted,true);
 requests[1].resolve('paused');await next;requests[0].resolve('recording');await old;
 assert.deepEqual(applied,['paused']);assert.equal(failed.length,0);assert.equal(checking,1);polling.close();
});
test('Closing or changing session prevents old completion or failure reaching the new workspace',async()=>{
 const pending=deferred(),events=[];let oldSignal;
 const old=new StatusPolling(signal=>{oldSignal=signal;return pending.promise},v=>events.push(v),e=>events.push(e));
 const task=old.poll();old.close();assert.equal(oldSignal.aborted,true);
 const current=new StatusPolling(async()=>'new session',v=>events.push(v),e=>events.push(e));
 await current.poll();pending.reject(Error('old failure'));await task;await old.poll();
 assert.deepEqual(events,['new session']);current.close();
});
test('Read failure permits the next poll and does not issue any capture operation',async()=>{
 const events=[];let count=0;
 const polling=new StatusPolling(async()=>{if(++count===1)throw Error('offline');return 'recording'},v=>events.push(v),()=>events.push('unavailable'));
 await polling.poll();await polling.poll();assert.deepEqual(events,['unavailable','recording']);polling.close();
});
test('Legacy and v1 status remain compatible; unknown contracts cannot claim healthy capture',()=>{
 const runtime={phase:'recording',capture:{state:'recording'}};
 for(const patch of [{},{schema_version:1}])assert.equal(coordinateRecording({...runtime,...patch},'recording',false).phase,'recording');
 const future=coordinateRecording({...runtime,schema_version:2},'recording',false);
 assert.equal(future.phase,'unknown');assert.equal(future.capture,'unknown');assert.equal(future.unavailable,true);
 assert.equal(coordinateRecording(runtime,'failed',false).phase,'unknown');
 assert.equal(coordinateRecording(runtime,'recording',true).phase,'unknown');
 assert.equal(coordinateRecording(runtime,'finished',true).phase,'saved');
});

test('Pause/resume disagreement between local confirmation and cached health remains unknown',()=>{
 assert.equal(coordinateRecording({phase:'paused',capture:{state:'paused'}},'recording',false).phase,'unknown');
 assert.equal(coordinateRecording({phase:'recording',capture:{state:'recording'}},'paused',false).capture,'unknown');
 assert.equal(coordinateRecording({phase:'paused',capture:{state:'paused'}},'paused',false).phase,'paused');
});
