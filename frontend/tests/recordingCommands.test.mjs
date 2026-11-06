import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const compiled=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));}
const {RecordingCommandClient}=await load('../src/features/notes/recordingCommands.ts');
const tick=()=>new Promise(setImmediate);
const reply=body=>({state:'paused',control:{version:body.expected_version+1},receipt:{request_id:body.request_id},native:{state:'paused',duration:1}});
const definitive=e=>e.code==='version_conflict';
const idFactory=()=>{let n=0;return ()=>String(++n).padStart(32,'0')};
test('Duplicate clicks share one command and never fetch separate initial versions',async()=>{
 let reads=0,sends=0,resolve;const wait=new Promise(r=>resolve=r);
 const client=new RecordingCommandClient(async()=>{reads++;return 0},async(_id,body)=>{sends++;await wait;return reply(body)},definitive,idFactory());
 const first=client.execute('one','pause'),second=client.execute('one','pause');await tick();
 assert.equal(reads,1);assert.equal(sends,1);resolve();assert.deepEqual(await first,await second);
});
test('Lost response retry retains the same request ID and original expected version',async()=>{
 const bodies=[];let reads=0;
 const client=new RecordingCommandClient(async()=>{reads++;return 3},async(_id,body)=>{bodies.push(body);if(bodies.length===1)throw Error('lost response');return reply(body)},definitive,idFactory());
 await assert.rejects(client.execute('one','pause'),/lost response/);
 await client.execute('one','pause');assert.equal(reads,1);assert.deepEqual(bodies[0],bodies[1]);
});
test('A definitive conflict never retries automatically; next explicit action gets a new identity',async()=>{
 const bodies=[];let version=1;
 const client=new RecordingCommandClient(async()=>version++,async(_id,body)=>{bodies.push(body);if(bodies.length===1)throw Object.assign(Error('changed'),{code:'version_conflict'});return reply(body)},definitive,idFactory());
 await assert.rejects(client.execute('one','pause'),/changed/);assert.equal(bodies.length,1);
 await client.execute('one','pause');assert.notEqual(bodies[0].request_id,bodies[1].request_id);assert.equal(bodies[1].expected_version,2);
});
test('An uncertain native action blocks opposite action but permits explicit stop',async()=>{
 const bodies=[];
 const client=new RecordingCommandClient(async()=>bodies.length,async(_id,body)=>{bodies.push(body);if(body.command==='pause')throw Error('uncertain');return reply(body)},definitive,idFactory());
 await assert.rejects(client.execute('one','pause'));
 await assert.rejects(client.execute('one','resume'),/未确认/);assert.equal(bodies.length,1);
 await client.execute('one','stop');assert.equal(bodies[1].command,'stop');assert.notEqual(bodies[0].request_id,bodies[1].request_id);
});
test('Browser reports follow observed local state without pretending to control its recorder',async()=>{
 const bodies=[];
 const client=new RecordingCommandClient(async()=>bodies.length,async(_id,body)=>{bodies.push(body);if(body.command==='pause')throw Error('offline');return reply(body)},definitive,idFactory());
 await assert.rejects(client.execute('one','pause','paused'));
 await client.execute('one','resume','recording');assert.equal(bodies[1].observed_state,'recording');assert.equal(bodies[1].expected_version,1);
});
test('Different commands cannot overtake an in-flight action; recordings remain independent',async()=>{
 let resolve;const wait=new Promise(r=>resolve=r),ids=[];
 const client=new RecordingCommandClient(async()=>0,async(id,body)=>{ids.push(id);if(id==='one')await wait;return reply(body)},definitive,idFactory());
 const first=client.execute('one','pause');await tick();await assert.rejects(client.execute('one','stop'),/处理中/);
 await client.execute('two','pause');assert.deepEqual(ids,['one','two']);resolve();await first;
});
test('API adapter sends versioned commands and exposes structured conflict messages',async()=>{
 const {api,APIError}=await load('../src/api.ts');const previous=globalThis.fetch;const calls=[];
 globalThis.fetch=async(path,init)=>{
  calls.push(path);
  if(path.endsWith('/health'))return new Response(JSON.stringify({runtime:{control:{version:7}}}));
  const body=JSON.parse(init.body);assert.equal(body.expected_version,7);assert.match(body.request_id,/^[0-9a-f]{32}$/);
  return new Response(JSON.stringify({detail:{code:'version_conflict',message:'录音状态已变化'}}),{status:409});
 };
 try{await assert.rejects(api.nativeCommand('http-fixture','pause'),e=>e instanceof APIError&&e.status===409&&e.code==='version_conflict'&&e.message==='录音状态已变化');assert.deepEqual(calls,['/api/recordings/http-fixture/health','/api/recordings/http-fixture/commands']);}
 finally{globalThis.fetch=previous;}
});
test('A synchronous transport exception cannot leave a permanently rejected in-flight entry',async()=>{
 let sends=0;
 const client=new RecordingCommandClient(async()=>0,(_id,body)=>{if(++sends<3)throw Error('sync transport');return Promise.resolve(reply(body))},definitive,idFactory());
 await assert.rejects(client.execute('one','pause'));
 await assert.rejects(client.execute('one','pause'));
 await client.execute('one','pause');assert.equal(sends,3);
});
test('Replayed old pause cannot make the UI adopt paused after capture already resumed',async()=>{
 const {api}=await load('../src/api.ts');const previous=globalThis.fetch;let sends=0;
 globalThis.fetch=async(path)=>{
  if(path.endsWith('/health'))return new Response(JSON.stringify({runtime:{control:{version:2}}}));
  sends++;
  return new Response(JSON.stringify({state:'recording',control:{version:2},receipt:{replayed:true,state:'paused',version:1},native:{state:'recording',duration:1}}));
 };
 try{await assert.rejects(api.nativeCommand('old-pause-fixture','pause'),/状态已变化/);assert.equal(sends,1);}
 finally{globalThis.fetch=previous;}
});
