import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/management/recordingValidationLabels.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {observationEndReason,observationStateLabel}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Normal recording end and observer stop have distinct explanations',()=>{
 assert.match(observationEndReason('recording_stopped'),/采集器已确认停止/);
 assert.match(observationEndReason('user_stopped_observation'),/不会停止录音/);
 assert.match(observationEndReason('target_elapsed'),/录音不受影响/);
 assert.doesNotMatch(observationEndReason('recording_stopped'),/验证通过|全程完整/);
});
test('Interruptions explain missing evidence without diagnosing contention',()=>{
 for (const reason of ['capture_not_running','capture_ended','service_restart','service_shutdown','observer_not_attached']) {
   assert.doesNotMatch(observationEndReason(reason),/验证通过|抢占|全程完整/);
 }
 assert.match(observationEndReason('capture_not_running'),/无法确认/);
 assert.match(observationEndReason('service_restart'),/中间状态未知/);
});
test('Legacy and unknown reasons never leak arbitrary strings',()=>{
 assert.match(observationEndReason(null),/未记录/);
 assert.match(observationEndReason('private-path'),/未识别/);
 assert.doesNotMatch(observationEndReason('private-path'),/private/);
});

test('Collapsed summaries distinguish recording end and retain legacy interruption state',()=>{
 assert.match(observationStateLabel('stopped','recording_stopped'),/录音已结束/);
 assert.equal(observationStateLabel('stopped','user_stopped_observation'),'已停止观察');
 assert.equal(observationStateLabel('interrupted','recording_stopped'),'观察已中断');
 assert.equal(observationStateLabel('private-state'),'状态待核对');
});
