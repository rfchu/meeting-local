import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/liveSession.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {watchLiveSession}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const original=globalThis.fetch;
const snapshot={type:'snapshot',seq:5,segments:[{start:0,end:3,text:'已保存'}],partial:{}};
async function read(events,cursor=0){
  const received=[];const bytes=new TextEncoder().encode(events.map(e=>JSON.stringify(e)).join('\n')+'\n');
  globalThis.fetch=async()=>new Response(new ReadableStream({start(c){for(const byte of bytes)c.enqueue(new Uint8Array([byte]));c.close();}}));
  try{await watchLiveSession('synthetic',cursor,e=>received.push(e),new AbortController().signal);return received;}finally{globalThis.fetch=original;}
}
test('Authoritative snapshot, Unicode tail revision and final replay arrive once',async()=>{
  const partial={type:'partial',seq:6,partial:{start:3,end:6,text:'文字修订🙂',stable_text:'文字'}};
  const final={type:'final',seq:7,result:{segments:[{start:3,end:6,text:'文字修订🙂'}],next_offset:6}};
  const received=await read([snapshot,partial,partial,final,{type:'heartbeat',seq:7},{type:'ended',seq:7}]);
  assert.deepEqual(received.map(e=>e.type),['snapshot','partial','final','ended']);
  assert.equal(received[1].partial.text,'文字修订🙂');
});
test('Missing sequence triggers reconnect instead of silently appending text',async()=>{
  await assert.rejects(read([snapshot,{type:'status',seq:7,message:'缺了一条'}]),/事件缺失/);
});
test('Expired journal can replace an old cursor with a fresh authoritative snapshot',async()=>{
  const received=await read([{...snapshot,seq:300},{type:'ended',seq:300}],2);
  assert.equal(received[0].seq,300);
});
test('Abrupt transport end and malformed confirmation never masquerade as completion',async()=>{
  await assert.rejects(read([snapshot]),/连接中断/);
  await assert.rejects(read([snapshot,{type:'final',seq:6,result:{segments:'错误'}}]),/确认结果无效/);
});
test('Unified envelope accepts Whisper finals and rejects foreign recordings and protocols',async()=>{
  const base={protocol_version:1,recording_id:'synthetic',engine:'whisper'};
  const received=await read([{...snapshot,...base},{...base,type:'final',seq:6,result:{segments:[{start:0,end:15,text:'分段结果'}]}},{...base,type:'ended',seq:6}]);
  assert.equal(received[1].engine,'whisper');
  await assert.rejects(read([{...snapshot,...base,recording_id:'other'}]),/其他录音/);
  await assert.rejects(read([{...snapshot,...base,protocol_version:99}]),/协议版本/);
  await assert.rejects(read([{...snapshot,...base,engine:'unknown'}]),/引擎无效/);
});
