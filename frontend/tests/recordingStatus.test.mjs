import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/recordingStatus.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {recordingStatus,lifecycleLabel}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const runtime={phase:'recording',observed_at:1,capture:{state:'recording',heartbeat_age_seconds:0},storage:{state:'writing',saved_bytes:32000,last_saved_seconds:10,saved_chunks:0,missing_chunks:0},asr:{state:'failed',engine:'qwen',covered_seconds:5,gap_count:1}};
test('ASR failure leaves the capture and saved position visibly separate',()=>{
 const view=recordingStatus(runtime,'recording',false);
 assert.equal(view.label,'录音中');assert.equal(view.input,'正在采集');assert.match(view.asr,/识别失败/);assert.match(view.savedPosition,/00:10/);
});
test('Disconnected UI never shows a cached healthy capture or inference state',()=>{
 const view=recordingStatus(runtime,'recording',true);
 assert.equal(view.phase,'unknown');assert.match(view.input,/不能确认/);assert.match(view.asr,/暂不可用/);assert.match(view.savedPosition,/00:10/);
});
test('Unknown heartbeat and confirmed interruption have distinct labels',()=>{
 assert.equal(recordingStatus({...runtime,phase:'unknown',capture:{state:'unknown'}},'recording',false).label,'状态未知');
 assert.equal(recordingStatus({...runtime,phase:'interrupted',capture:{state:'interrupted'}},'failed',false).label,'录音已中断');
});
test('An observed recorder error cannot be covered by a cached healthy server snapshot',()=>{
 const view=recordingStatus(runtime,'failed',false);
 assert.equal(view.label,'状态未知');assert.match(view.input,/不能确认/);
});
test('Save retry never implies the audio must be recorded again',()=>{
 assert.equal(recordingStatus({...runtime,phase:'saving',capture:{state:'stopped'}},'failed',false).label,'保存待重试');
 assert.equal(recordingStatus(runtime,'finished',false).label,'会议已保存');
});
test('Browser chunks do not fabricate an exact saved audio timestamp',()=>{
 const view=recordingStatus({...runtime,storage:{...runtime.storage,last_saved_seconds:null,saved_chunks:3}},'recording',false);
 assert.match(view.savedPosition,/3 段/);assert.match(view.savedPosition,/待合并核对/);
});
test('Lifecycle UI explains unknown exits without guessing an OOM or sleep cause',()=>{
 assert.match(lifecycleLabel('service.previous_unclosed'),/原因待核查/);
 assert.equal(lifecycleLabel('asr.failed'),'实时识别：发生失败');
 assert.equal(lifecycleLabel('unknown.secret-content'),'运行：状态已变化');
});

test('An alive capture heartbeat cannot hide missing microphone audio',()=>{
 const view=recordingStatus({...runtime,capture:{...runtime.capture,tracks:[{name:'system',state:'receiving'},{name:'microphone',state:'no_data'}]}},'recording',false);
 assert.equal(view.phase,'recording');assert.equal(view.label,'录音输入待检查');assert.match(view.input,/未收到音频/);
 assert.match(lifecycleLabel('microphone_input.no_data'),/麦克风音轨/);
 assert.match(lifecycleLabel('page.detached'),/继续托管/);
});

test('Unconfirmed disk progress is visible even while audio callbacks are healthy',()=>{
 const view=recordingStatus({...runtime,storage:{...runtime.storage,state:'unconfirmed'}},'recording',false);
 assert.equal(view.label,'录音保存待核对');
 assert.match(lifecycleLabel('storage.unconfirmed'),/文件：保存状态待核对/);
 assert.match(lifecycleLabel('service.observation_gap'),/观测出现较长间隔/);
 assert.equal(recordingStatus({...runtime,storage:{...runtime.storage,state:'unconfirmed'}},'recording',true).label,'状态未知');
});
