import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/recordingEnd.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {QuietMonitor,endCountdown}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
function feed(monitor,from,to,quiet=true){for(let t=from;t<=to;t+=1000)monitor.observe(t,quiet);}
test('Five minutes of fresh quiet evidence reminds, while speech resets it',()=>{
 const m=new QuietMonitor();feed(m,0,299000);
 assert.equal(m.shouldRemind(299999),false);m.observe(300000,true);assert.equal(m.shouldRemind(300000),true);
 m.observe(301000,false);assert.equal(m.shouldRemind(301000),false);
});
test('Missing callbacks, unavailable audio and background suspension are not silence',()=>{
 const m=new QuietMonitor();feed(m,0,290000);
 assert.equal(m.shouldRemind(310001),false);
 m.observe(310001,true);assert.equal(m.shouldRemind(310001),false);
 feed(m,311001,611001);assert.equal(m.shouldRemind(611001),true);
 m.observe(612001,true,false);assert.equal(m.shouldRemind(612001),false);
});
test('Snooze expires after five minutes; reset discards old sessions',()=>{
 const m=new QuietMonitor();feed(m,0,300000);m.snooze(300000);
 feed(m,301000,599000);assert.equal(m.shouldRemind(599000),false);
 m.observe(600000,true);assert.equal(m.shouldRemind(600000),true);
 m.reset();assert.equal(m.shouldRemind(600000),false);
});
test('Explicit countdown ends once due; hidden and suspended pages cancel instead',()=>{
 assert.deepEqual(endCountdown(5000,0,true),{state:'waiting',seconds:5});
 assert.deepEqual(endCountdown(5000,4999,true),{state:'waiting',seconds:1});
 assert.equal(endCountdown(5000,5000,true).state,'finish');
 assert.equal(endCountdown(5000,1000,false).state,'cancelled');
 assert.equal(endCountdown(5000,8000,true).state,'cancelled');
});
