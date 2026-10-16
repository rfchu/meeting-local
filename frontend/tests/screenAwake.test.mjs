import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const compiled=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));}
const {ScreenAwake}=await load('../src/features/notes/screenAwake.ts');
const {queueAwake}=await load('../src/features/notes/awakeQueue.ts');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Page extends EventTarget {visibilityState='visible';}
class Lock extends EventTarget {
  released=false;
  async release(){this.released=true;this.dispatchEvent(new Event('release'));}
}
test('Acquire once, recover after visibility returns, and release on stop',async()=>{
  const page=new Page(),locks=[],states=[];
  const awake=new ScreenAwake(page,async()=>{const lock=new Lock();locks.push(lock);return lock;},value=>states.push(value));
  await awake.ensure();await awake.ensure();assert.equal(locks.length,1);assert.equal(states.at(-1),true);
  page.visibilityState='hidden';await locks[0].release();assert.equal(states.at(-1),false);
  await awake.ensure();assert.equal(locks.length,1);
  page.visibilityState='visible';page.dispatchEvent(new Event('visibilitychange'));await tick();
  assert.equal(locks.length,2);assert.equal(states.at(-1),true);
  awake.stop();assert.equal(locks[1].released,true);
  page.dispatchEvent(new Event('visibilitychange'));await tick();assert.equal(locks.length,2);
});
test('Late browser permission response after stop is released immediately',async()=>{
  let resolve;const lock=new Lock(),states=[];
  const awake=new ScreenAwake(new Page(),()=>new Promise(r=>resolve=r),value=>states.push(value));
  const pending=awake.ensure();awake.stop();resolve(lock);await pending;
  assert.equal(lock.released,true);assert.deepEqual(states,[]);
});
test('Unsupported, rejected and already released locks never report protection',async()=>{
  const page=new Page(),states=[];
  const unsupported=new ScreenAwake(page,undefined,value=>states.push(value));await unsupported.ensure();unsupported.stop();
  const rejected=new ScreenAwake(page,async()=>{throw Error('denied');},value=>states.push(value));await rejected.ensure();rejected.stop();
  const lock=new Lock();lock.released=true;
  const stale=new ScreenAwake(page,async()=>lock,value=>states.push(value));await stale.ensure();stale.stop();
  assert.ok(states.every(value=>value===false));
});
test('Stop and rapid toggle cannot overtake a pending server renewal',async()=>{
  const events=[];let resolve;
  const pending=queueAwake('recording',async()=>{events.push('acquire');await new Promise(r=>resolve=r);});
  await tick();
  const release=queueAwake('recording',async()=>{events.push('release');});
  const reacquire=queueAwake('recording',async()=>{events.push('reacquire');});
  assert.deepEqual(events,['acquire']);resolve();await Promise.all([pending,release,reacquire]);
  assert.deepEqual(events,['acquire','release','reacquire']);
  await assert.rejects(queueAwake('recording',async()=>{throw Error('offline');}));
  await queueAwake('recording',async()=>events.push('release after failure'));
  assert.equal(events.at(-1),'release after failure');
});
