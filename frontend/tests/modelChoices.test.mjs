import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/preferences/modelChoices.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readModelChoice,saveModelChoice,resolveModelChoice}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Selections survive reopening and remain independent by purpose',()=>{
  const values=new Map();const storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
  for(const [slot,value] of Object.entries({analysis:'local-a',minutes:'cloud-b',notes:'local-c',speakers:'local-d',qa:'local-e',transcription:'qwen'})) saveModelChoice(slot,value,storage);
  assert.equal(readModelChoice('analysis',storage),'local-a');
  assert.equal(readModelChoice('minutes',storage),'cloud-b');
  assert.equal(readModelChoice('transcription',storage),'qwen');
  saveModelChoice('minutes','local-a',storage);
  assert.equal(readModelChoice('qa',storage),'local-e');
  assert.equal(readModelChoice('minutes',storage),'local-a');
});
test('Removed configurations fall back to a valid default without selecting nonexistent IDs',()=>{
  assert.equal(resolveModelChoice('gone',['a','b'],'b'),'b');
  assert.equal(resolveModelChoice('b',['a','b'],'a'),'b');
  assert.equal(resolveModelChoice('gone',['a','b'],'also-gone'),'a');
  assert.equal(resolveModelChoice('gone',[],'a'),'');
});
test('Blocked storage and invalid stored values do not break model selection',()=>{
  assert.equal(readModelChoice('analysis',{getItem:()=>{throw Error('blocked')}}),'');
  assert.equal(readModelChoice('analysis',{getItem:()=> 'x'.repeat(501)}),'');
  assert.equal(saveModelChoice('analysis','a',{setItem:()=>{throw Error('full')}}),false);
  assert.equal(readModelChoice('analysis',undefined),'');
});
