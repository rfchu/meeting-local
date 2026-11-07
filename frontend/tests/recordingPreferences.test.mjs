import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/notes/recordingPreferences.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readRecordingPreferences,saveRecordingPreferences,DEFAULT_RECORDING_PREFERENCES,transcriptionOptions}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Quiet reminder defaults safely and remembers explicit opt-out',()=>{
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1})}).silenceReminder,true);
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1,silenceReminder:false})}).silenceReminder,false);
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1,silenceReminder:'false'})}).silenceReminder,true);
});
test('Last used source, engine, streaming and completion choice survive reopening',()=>{
  let raw=null;const storage={getItem:()=>raw,setItem:(_,value)=>{raw=value;}};
  const selected={...DEFAULT_RECORDING_PREFERENCES,version:1,keepAwake:true,autoProcess:true,postGoal:"transcript",captureSource:'macos-both',draftEngine:'qwen',streamEnabled:true,responsiveMode:true,completionMode:'none'};
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
test('post-recording automatic handoff defaults on and persists explicit opt-out',()=>{
  const storage={getItem:()=>JSON.stringify({version:1,completionMode:'none',autoProcess:false})};
  assert.equal(readRecordingPreferences(storage).autoProcess,false);
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1})}).autoProcess,true);
  assert.equal(readRecordingPreferences(storage).completionMode,'none');
});
test('automatic minutes goal persists without changing the legacy default',()=>{
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1,postGoal:'minutes'})}).postGoal,'minutes');
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1})}).postGoal,'transcript');
});
test('Context and optional local correction choices persist; old settings retain explicit responsive choice',()=>{
  const choices={...DEFAULT_RECORDING_PREFERENCES,contextEnabled:false,recognitionTerms:'听记、Qwen',correctionEnabled:true,correctionProfileId:'local-fixture',responsiveMode:false};
  let raw;const storage={getItem:()=>raw,setItem:(_,value)=>raw=value};
  saveRecordingPreferences(storage,choices);
  assert.deepEqual(readRecordingPreferences(storage),choices);
  const legacy=readRecordingPreferences({getItem:()=>JSON.stringify({version:1,responsiveMode:false})});
  assert.equal(legacy.responsiveMode,false);
  assert.equal(legacy.contextEnabled,true);
  assert.equal(legacy.correctionEnabled,false);
  assert.equal(legacy.correctionProfileId,'');
});
test('Prefix continuation defaults on for old preferences and remembers explicit opt-out',()=>{
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1})}).prefixEnabled,true);
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1,prefixEnabled:false})}).prefixEnabled,false);
});

test('language choices persist independently and invalid values use supported defaults',()=>{
  const selected={...DEFAULT_RECORDING_PREFERENCES,sourceLanguage:'en',outputLanguage:'zh'};
  let value='';
  const storage={getItem:()=>value,setItem:(_,next)=>{value=next;}};
  assert.equal(saveRecordingPreferences(storage,selected),true);
  assert.deepEqual(readRecordingPreferences(storage),selected);
  assert.equal(readRecordingPreferences({getItem:()=>JSON.stringify({version:1,sourceLanguage:'auto',outputLanguage:'en'})}).sourceLanguage,'auto');
  const invalid=readRecordingPreferences({getItem:()=>JSON.stringify({version:1,sourceLanguage:'fr',outputLanguage:'auto'})});
  assert.equal(invalid.sourceLanguage,'auto');
  assert.equal(invalid.outputLanguage,'zh');
});
