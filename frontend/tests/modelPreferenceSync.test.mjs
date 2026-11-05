import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/preferences/useModelChoice.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
let count=0;
async function fresh(){return import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64')+'#'+(++count));}
const fetchOriginal=globalThis.fetch,windowOriginal=globalThis.window;
async function environment(values,run){
 globalThis.window={localStorage:{getItem:k=>values[k]||null,setItem:(k,v)=>{values[k]=v;}}};
 try{await run();}finally{globalThis.fetch=fetchOriginal;globalThis.window=windowOriginal;}
}
test('Legacy model migration sends only choices and marks success after server acknowledgement',async()=>{
 await environment({'tingji.model-choice.v1.minutes':'local-one','tingji.recording.preferences.v1':JSON.stringify({version:1,draftEngine:'qwen',correctionProfileId:'local-two',api_key:'never-upload',cloudConsent:true})},async()=>{
  const calls=[];globalThis.fetch=async(path,init)=>{calls.push([path,init&&JSON.parse(init.body)]);return Response.json({roles:{},profiles:[]});};
  const module=await fresh();await module.loadModelPreferences();
  assert.deepEqual(calls[0][1],{choices:{minutes:'local-one',live_transcription:'qwen',correction:'local-two'}});
  assert.equal(window.localStorage.getItem('tingji.model-preferences.imported.v1'),'yes');
  assert.equal(calls[1][0],'/api/model-preferences');
 });
});
test('Failed migration can retry and never marks import as complete',async()=>{
 await environment({},async()=>{
  let fail=true;globalThis.fetch=async()=>fail?Response.json({detail:'offline'},{status:503}):Response.json({roles:{},profiles:[]});
  const module=await fresh();await assert.rejects(module.loadModelPreferences(),/offline/);
  assert.equal(window.localStorage.getItem('tingji.model-preferences.imported.v1'),null);
  fail=false;await module.loadModelPreferences();assert.equal(window.localStorage.getItem('tingji.model-preferences.imported.v1'),'yes');
 });
});
test('Blocked browser storage still permits server preferences and versioned updates',async()=>{
 await environment({},async()=>{
  window.localStorage.getItem=()=>{throw new Error('blocked');};window.localStorage.setItem=()=>{throw new Error('blocked');};
  const calls=[];globalThis.fetch=async(path,init)=>{calls.push([path,init&&JSON.parse(init.body)]);return Response.json({roles:{},profiles:[]});};
  const module=await fresh();await module.loadModelPreferences();await module.saveModelPreference('overview','default','model-id',3);
  assert.deepEqual(calls.at(-1),['/api/model-preferences/overview/default',{value:'model-id',version:3}]);
 });
});
