import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
async function load(path){const r=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(r.outputFiles[0].text).toString('base64'));}
const {recordingStatus,controlPresentation,recordingActive}=await load('../src/features/notes/recordingStatus.ts');
const {reconcileNativePhase}=await load('../src/features/notes/recordingCoordinator.ts');
const events=await load('../src/features/notes/recordingCommandEvents.ts');
const runtime={schema_version:1,source:'native',phase:'recording',capture:{state:'recording'},storage:{state:'writing',last_saved_seconds:10,saved_chunks:0},asr:{state:'disabled'},control:{version:1,status:'unconfirmed',pending_command:'pause'}};
test('Workbench and read-only consumers share labels and independent ASR/storage states',()=>{
 const workbench=recordingStatus(runtime,'recording',false),other=recordingStatus(runtime,'',false);
 assert.equal(workbench.label,'控制结果待确认');
 for(const field of ['label','input','storage','asr','savedPosition'])assert.equal(workbench[field],other[field]);
 assert.match(other.asr,/未开启/);assert.match(other.storage,/落盘/);
});
test('Unconfirmed server intent survives local dismissal and failed refresh; idle alone creates no claim of valid audio',()=>{
 events.reportRecordingCommand('a','conflict');events.dismissRecordingCommandFeedback('a');
 assert.equal(events.recordingCommandFeedback('a'),null);
 assert.equal(controlPresentation(runtime,false).persistent,true);
 assert.match(controlPresentation(runtime,true).label,/上次记录/);
 const stale=recordingStatus(runtime,'',true);assert.equal(stale.label,'状态未知');assert.match(stale.asr,/暂不可用/);
 assert.equal(controlPresentation({...runtime,control:{status:'idle'}},false),null);
});
test('Transient conflict stays separate from persisted uncertainty and can be dismissed without executing commands',()=>{
 let notices=0;const unwatch=events.watchRecordingCommands(()=>notices++);
 events.reportRecordingCommand('conflict','conflict');const feedback=events.recordingCommandFeedback('conflict');
 assert.match(controlPresentation(undefined,false,feedback.kind).label,/状态已变化/);
 assert.equal(controlPresentation(undefined,false,feedback.kind).persistent,false);
 events.dismissRecordingCommandFeedback('conflict');assert.equal(notices,2);assert.equal(events.recordingCommandFeedback('conflict'),null);
 unwatch();events.reportRecordingCommand('conflict');assert.equal(notices,2);
});
test('Cross-tab hints carry refresh identity only and never become control commands',()=>{
 const oldWindow=globalThis.window,oldChannel=globalThis.BroadcastChannel;let channel;
 class FakeChannel{constructor(){channel=this;}postMessage(value){this.sent=value;}}
 globalThis.window={};globalThis.BroadcastChannel=FakeChannel;
 const received=[];const unwatch=events.watchRecordingCommands(id=>received.push(id));const id='b'.repeat(32);
 try{
  channel.onmessage({data:{type:'command',recording_id:id,command:'stop'}});
  channel.onmessage({data:{type:'changed',recording_id:'invalid'}});assert.deepEqual(received,[]);
  channel.onmessage({data:{type:'changed',recording_id:id}});assert.deepEqual(received,[id]);
  events.reportRecordingCommand(id,'unconfirmed');assert.deepEqual(channel.sent,{type:'changed',recording_id:id});
  assert.deepEqual(Object.keys(events.recordingCommandFeedback(id)),['kind']);
 }finally{unwatch();globalThis.window=oldWindow;globalThis.BroadcastChannel=oldChannel;}
});
test('Native observations adopt peer pause/resume but never revive a closing or failed workspace',()=>{
 assert.equal(reconcileNativePhase('recording','paused'),'paused');assert.equal(reconcileNativePhase('paused','recording'),'recording');
 for(const local of ['saving','finished','failed','idle'])assert.equal(reconcileNativePhase(local,'recording'),local);
 assert.equal(reconcileNativePhase('recording','starting'),'recording');
});
test('Fresh browser capture joins active groups, while stale projection and legacy records remain recoverable candidates',()=>{
 assert.equal(recordingActive({native_running:false,runtime:{...runtime,source:'browser'}}),true);
 assert.equal(recordingActive({native_running:false,runtime:{...runtime,capture:{state:'unknown'}}}),false);
 assert.equal(recordingActive({native_running:false}),false);assert.equal(recordingActive({native_running:true}),true);
});
test('Stopping capture alone does not claim that meeting publication is running or finished',()=>{
 const stopped={...runtime,phase:'saving',capture:{state:'stopped'},control:{status:'idle'},storage:{...runtime.storage,state:'available'}};
 assert.match(recordingStatus(stopped,'',false).label,/等待保存/);
 assert.equal(recordingStatus(stopped,'saving',false).label,'正在保存会议');
 assert.doesNotMatch(recordingStatus(stopped,'',false).label,/已保存/);
});
test('Empty interrupted sessions with pending controls stay visible, and active browser sessions do not count as recoveries',async()=>{
 const {emptyRecording,recordingRecoveryCount}=await load('../src/features/notes/recordingInventory.ts');
 const empty={source:'macos',state:'interrupted',notes:[],chunk_count:0,health:{state:'interrupted',native_running:false,empty_session:true,audio:{saved_bytes:0},runtime:{...runtime,phase:'interrupted',capture:{state:'interrupted'}}}};
 assert.equal(emptyRecording(empty),false);assert.equal(recordingRecoveryCount([empty]),1);
 const browser={...empty,source:undefined,health:{...empty.health,runtime:{...runtime,source:'browser'}}};
 assert.equal(recordingRecoveryCount([browser]),0);
});
