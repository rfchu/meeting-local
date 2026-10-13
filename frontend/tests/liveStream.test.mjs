import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
const compiled = await build({ entryPoints: [new URL('../src/features/notes/liveStream.ts', import.meta.url).pathname], bundle: true, format: 'esm', write: false });
const { readLiveStream } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const originalFetch = globalThis.fetch;
const base = {request_id:'request-one',start:15,end:30};
const final = {segments:[{start:15,end:30,text:'会议确认。'}],duration:15,processing_ms:20};
async function read(events, onPartial = () => {}) {
  const bytes = new TextEncoder().encode(events.map(e=>JSON.stringify(e)).join('\n')+'\n');
  globalThis.fetch = async () => new Response(new ReadableStream({start(controller){
    for(const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  }}));
  try { return await readLiveStream('/test',{},onPartial); }
  finally { globalThis.fetch = originalFetch; }
}
test('Unicode split across bytes and duplicate events preserve final text', async () => {
  const partials=[];
  const result=await read([{...base,type:'start',seq:0},
    {...base,type:'partial',seq:1,text:'会议确认。'},
    {...base,type:'partial',seq:1,text:'重复事件'},
    {...base,type:'final',seq:2,result:final}], p=>partials.push(p.text));
  assert.deepEqual(partials,['会议确认。']);assert.deepEqual(result,final);
});
test('Missing final does not return partial as a completed result', async () => {
  await assert.rejects(read([{...base,type:'start',seq:0},{...base,type:'partial',seq:1,text:'部分文字'}]),/提前结束/);
});
test('Sequence gaps and mixed request identities are rejected', async () => {
  await assert.rejects(read([{...base,type:'start',seq:0},{...base,type:'final',seq:2,result:final}]),/序列缺失/);
  await assert.rejects(read([{...base,type:'start',seq:0},{...base,request_id:'other',type:'final',seq:1,result:final}]),/标识不一致/);
});
test('Error after partial rejects the request', async () => {
  await assert.rejects(read([{...base,type:'start',seq:0},{...base,type:'partial',seq:1,text:'部分'},
    {...base,type:'error',seq:2,message:'模型失败'}]),/模型失败/);
});
