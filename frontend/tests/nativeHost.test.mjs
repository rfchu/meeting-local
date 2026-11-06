import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {build} from 'esbuild';
const built=await build({entryPoints:[new URL('../src/preferences/nativeHost.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {embeddedHost}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
test('Native presentation hint is versioned and ordinary browsers keep their behavior',()=>{
 assert.equal(embeddedHost({}),false);assert.equal(embeddedHost(null),false);
 assert.equal(embeddedHost({__TINGJI_HOST__:{version:2,embedded:true}}),false);
 assert.equal(embeddedHost({__TINGJI_HOST__:{version:1,embedded:true}}),true);
});
test('Permission script rejects APIs without invoking device or location code',async()=>{
 let calls=0;const scope={window:{},navigator:{mediaDevices:{getUserMedia:()=>calls++,getDisplayMedia:()=>calls++},geolocation:{getCurrentPosition:()=>calls++}},DOMException,Object,Promise};
 vm.runInNewContext(fs.readFileSync(new URL('../../native/Host/workspace-permissions.js',import.meta.url),'utf8'),scope);
 for(const name of ['getUserMedia','getDisplayMedia'])await assert.rejects(scope.navigator.mediaDevices[name](),{name:'NotAllowedError'});
 let denied=false;scope.navigator.geolocation.getCurrentPosition(()=>calls++,()=>denied=true);
 assert.equal(calls,0);assert.equal(denied,true);assert.equal(scope.window.__TINGJI_HOST__.web_capture,false);
 assert.equal(Object.getOwnPropertyDescriptor(scope.navigator.mediaDevices,'getUserMedia').configurable,false);
});
