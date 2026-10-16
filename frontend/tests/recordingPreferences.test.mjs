import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/recordingPreferences.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readRecordingPreferences,saveRecordingPreferences,DEFAULT_RECORDING_PREFERENCES,transcriptionOptions}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Last used source, engine, streaming and completion choice survive reopening',()=>{
  let raw=null;const storage={getItem:()=>raw,setItem:(_,value)=>{raw=value;}};
  const selected={version:1,keepAwake:true,captureSource:'macos-both',draftEngine:'qwen',streamEnabled:true,responsiveMode:true,completionMode:'none'};
  assert.equal(saveRecordingPreferences(storage,selected),true);
  assert.deepEqual(readRecordingPreferences(storage),selected);
});
test('Corrupt, old and unsupported preferences restore safe defaults',()=>{
  for(const value of ['bad', 'null',JSON.stringify({version:2}),JSON.stringify({version:1,keepAwake:true,captureSource:'remote',draftEngine:'unknown',streamEnabled:'yes',responsiveMode:'yes',completionMode:'delete'})]) {
    assert.deepEqual(readRecordingPreferences({getItem:()=>value}),DEFAULT_RECORDING_PREFERENCES);
  }
  assert.deepEqual(readRecordingPreferences({getItem:()=>{throw Error('denied');}}),DEFAULT_RECORDING_PREFERENCES);
  assert.equal(saveRecordingPreferences({setItem:()=>{throw Error('denied');}},DEFAULT_RECORDING_PREFERENCES),false);
});
test('Save-only does not enqueue ASR; quick and full retain intended engine and diarization',()=>{
  assert.equal(transcriptionOptions('none','qwen','whisper',true,3),null);
  assert.deepEqual(transcriptionOptions('quick','qwen','whisper',true,3),{engine:'qwen',completion_mode:'quick',diarization:false,num_speakers:3});
  assert.deepEqual(transcriptionOptions('full','qwen','whisper',true,3),{engine:'whisper',completion_mode:'full',diarization:true,num_speakers:3});
});
test('Older preferences enable keep-awake by default and an explicit opt-out survives',()=>{
  const old={...DEFAULT_RECORDING_PREFERENCES};delete old.keepAwake;
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify(old)}).keepAwake,true);
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({...old,keepAwake:false})}).keepAwake,false);
});
