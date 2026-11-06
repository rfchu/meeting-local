import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const result=await build({entryPoints:[new URL('../src/features/minutes/deliveryReadiness.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {deliveryReadyForView,deliveryIsCurrent}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
const meeting={revision:2,title:'合成交付',active_minutes_id:'adopted'};
const value={version:1,revision:2,artifact_id:'adopted',include_citations:false,review:{title:'合成交付'},current_delivery_id:'snapshot'};
test('Delivery check is bound to current adopted version, transcript, title and citations',()=>{
 assert.equal(deliveryReadyForView(value,meeting,'adopted',false,false),true);
 for (const patch of [{version:2},{revision:1},{artifact_id:'draft'},{review:{title:'旧主题'}},{include_citations:true}])assert.equal(deliveryReadyForView({...value,...patch},meeting,'adopted',false,false),false);
});
test('Unsaved local work and other selected drafts cannot use a delivery check',()=>{
 assert.equal(deliveryReadyForView(value,meeting,'adopted',true,false),false);
 assert.equal(deliveryReadyForView(value,meeting,'draft',false,false),false);
 assert.equal(deliveryReadyForView(undefined,meeting,'adopted',false,false),false);
});
test('Delivery identity is exact rather than matching only the adopted artifact',()=>{
 assert.equal(deliveryIsCurrent(value,'snapshot'),true);
 assert.equal(deliveryIsCurrent(value,'historical'),false);
 assert.equal(deliveryIsCurrent({...value,current_delivery_id:null},'snapshot'),false);
});
