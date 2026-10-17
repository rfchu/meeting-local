import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const compiled=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));}
const {watchRecordingRecovery}=await load('../src/features/notes/recordingRecovery.ts');
const {queueAwake}=await load('../src/features/notes/awakeQueue.ts');
const {api}=await load('../src/api.ts');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Page extends EventTarget {visibilityState='visible';}

test('Returning to the page, reconnecting and restoring history trigger recovery; cleanup detaches all listeners',()=>{
  const page=new Page(),target=new EventTarget();let calls=0;
  const unwatch=watchRecordingRecovery(()=>calls++,page,target);
  page.visibilityState='hidden';page.dispatchEvent(new Event('visibilitychange'));assert.equal(calls,0);
  page.visibilityState='visible';page.dispatchEvent(new Event('visibilitychange'));assert.equal(calls,1);
  target.dispatchEvent(new Event('online'));assert.equal(calls,2);
  target.dispatchEvent(new Event('pageshow'));assert.equal(calls,3);
  unwatch();unwatch();
  page.dispatchEvent(new Event('visibilitychange'));target.dispatchEvent(new Event('online'));target.dispatchEvent(new Event('pageshow'));
  assert.equal(calls,3);
});

test('A hung renewal times out and cannot permanently block release or a later renewal',async()=>{
  const originalFetch=globalThis.fetch,originalTimeout=AbortSignal.timeout;
  const controllers=[],limits=[],events=[];
  AbortSignal.timeout=ms=>{limits.push(ms);const controller=new AbortController();controllers.push(controller);return controller.signal;};
  globalThis.fetch=async(url,init)=>{
    const enabled=JSON.parse(init.body).enabled;events.push(enabled);
    if(events.length===1) return new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(init.signal.reason),{once:true}));
    return new Response(JSON.stringify({active:enabled,supported:true,error:''}));
  };
  try {
    const pending=queueAwake('timeout-fixture',()=>api.recordingKeepAwake('timeout-fixture',true));
    const rejected=assert.rejects(pending,{name:'TimeoutError'});
    await tick();
    const release=queueAwake('timeout-fixture',()=>api.recordingKeepAwake('timeout-fixture',false));
    assert.deepEqual(events,[true]);
    controllers[0].abort(new DOMException('Timed out','TimeoutError'));
    await rejected;assert.equal((await release).active,false);
    assert.equal((await queueAwake('timeout-fixture',()=>api.recordingKeepAwake('timeout-fixture',true))).active,true);
    assert.deepEqual(events,[true,false,true]);assert.deepEqual(limits,[10000,10000,10000]);
  } finally {globalThis.fetch=originalFetch;AbortSignal.timeout=originalTimeout;}
});

test('Native status timeout rejects a hung poll and permits the next poll',async()=>{
  const originalFetch=globalThis.fetch,originalTimeout=AbortSignal.timeout;
  let controller,calls=0;
  AbortSignal.timeout=ms=>{assert.equal(ms,10000);controller=new AbortController();return controller.signal;};
  globalThis.fetch=async(url,init)=>{
    assert.equal(url,'/api/native-capture/status-fixture');calls++;
    if(calls===1)return new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(init.signal.reason),{once:true}));
    return new Response(JSON.stringify({state:'recording',duration:30}));
  };
  try {
    const pending=api.nativeStatus('status-fixture');
    const rejected=assert.rejects(pending,{name:'TimeoutError'});
    controller.abort(new DOMException('Timed out','TimeoutError'));await rejected;
    assert.equal((await api.nativeStatus('status-fixture')).state,'recording');assert.equal(calls,2);
  } finally {globalThis.fetch=originalFetch;AbortSignal.timeout=originalTimeout;}
});
