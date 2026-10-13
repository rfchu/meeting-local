import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const compiled=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));}
const {responsivePlan,takeFrames}=await load('../src/features/notes/rollingAudio.ts');
test('Cumulative 5/10 second previews stay unconfirmed until max window',()=>{
  assert.deepEqual(responsivePlan(500,100,500,0),{count:500,confirmed:false});
  assert.equal(responsivePlan(600,100,600,500),null);
  assert.deepEqual(responsivePlan(1000,100,1000,500),{count:1000,confirmed:false});
  assert.deepEqual(responsivePlan(1800,100,1800,1000),{count:1500,confirmed:true});
});
test('Silence and pause finalize available tail without tiny windows',()=>{
  assert.deepEqual(responsivePlan(600,100,500,500),{count:600,confirmed:true});
  assert.equal(responsivePlan(40,100,40,0,true),null);
  assert.deepEqual(responsivePlan(120,100,120,0,true),{count:120,confirmed:true});
});
test('Taking a snapshot preserves PCM and confirmed consumption leaves exact remainder',()=>{
  const frames=[new Float32Array([1,2,3]),new Float32Array([4,5,6])];
  const {selected,remaining}=takeFrames(frames,4);
  assert.deepEqual(selected.flatMap(f=>[...f]),[1,2,3,4]);
  assert.deepEqual(remaining.flatMap(f=>[...f]),[5,6]);
  assert.deepEqual(frames.flatMap(f=>[...f]),[1,2,3,4,5,6]);
});
test('Native batch requests retain the selected Qwen engine',async()=>{
  const {api}=await load('../src/api.ts');const original=globalThis.fetch;let body;
  globalThis.fetch=async(url,init)=>{body=JSON.parse(init.body);return new Response(JSON.stringify({ready:false,segments:[]}));};
  try{await api.nativeDraft('fixture',0,'qwen');assert.equal(body.engine,'qwen');}
  finally{globalThis.fetch=original;}
});
