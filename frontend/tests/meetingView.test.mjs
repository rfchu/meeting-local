import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compile=async path=>{const output=await build({entryPoints:[new URL(path,import.meta.url).pathname],bundle:true,format:'esm',write:false});return import('data:text/javascript;base64,'+Buffer.from(output.outputFiles[0].text).toString('base64'));};
const {readMeetingView,saveMeetingView,resolveMeetingView,lastMeeting,rememberMeeting}=await compile('../src/preferences/meetingView.ts');
const {deliveryAttentionCount}=await compile('../src/components/deliveryAttention.ts');
const memory=()=>{const rows=new Map();return {getItem:k=>rows.get(k)||null,setItem:(k,v)=>rows.set(k,v),rows};};
test('meeting view merges playback and workspace changes while keeping different meetings independent',()=>{
  const storage=memory();saveMeetingView('a',{workspace:'transcript',minutesId:'old',position:35,rate:1.5,track:'system'},storage);
  saveMeetingView('b',{workspace:'minutes',position:9},storage);saveMeetingView('a',{workspace:'analysis'},storage);
  assert.deepEqual(readMeetingView('a',storage),{workspace:'analysis',minutesId:'old',position:35,rate:1.5,track:'system'});
  assert.equal(readMeetingView('b',storage).position,9);
  rememberMeeting('a',storage);assert.equal(lastMeeting(storage),'a');rememberMeeting('',storage);assert.equal(lastMeeting(storage),'');
});
test('resolveMeetingView covers audio-only, transcript-only, minutes-ready, and legacy bookmarks',()=>{
  const minutes={id:'m1',kind:'meeting_minutes',revision:1,stale:false};
  const audioOnly={revision:0,duration:30,artifacts:[],audio_tracks:['microphone']};
  const transcriptOnly={revision:1,duration:30,artifacts:[],audio_tracks:['microphone']};
  const withMinutes={revision:1,duration:30,active_minutes_id:'m1',artifacts:[minutes],audio_tracks:['microphone']};

  assert.equal(resolveMeetingView(audioOnly,{}).workspace,'transcript');
  assert.equal(resolveMeetingView(transcriptOnly,{}).workspace,'transcript');
  assert.equal(resolveMeetingView(withMinutes,{}).workspace,'minutes');

  const storage=memory();
  for (const legacy of ['notes','prepare','copilot']) {
    storage.setItem('tingji.meeting.views.v1',JSON.stringify({a:{workspace:legacy,position:5}}));
    const saved=readMeetingView('a',storage);
    assert.equal(saved.workspace,undefined);
    assert.equal(resolveMeetingView(audioOnly,saved).workspace,'transcript');
    assert.equal(resolveMeetingView(withMinutes,saved).workspace,'minutes');
    assert.equal(resolveMeetingView(audioOnly,{workspace:legacy}).workspace,'transcript');
  }
});
test('missing or foreign minutes fall back to adopted artifact; unavailable tracks and excessive position are corrected',()=>{
  const active={id:'active',kind:'meeting_minutes',revision:1,stale:false};const old={id:'old',kind:'meeting_minutes',revision:1,stale:true};
  const meeting={revision:1,duration:12,active_minutes_id:'active',artifacts:[active,old],audio_tracks:['system']};
  const restored=resolveMeetingView(meeting,{workspace:'transcript',minutesId:'foreign',position:99,track:'microphone',rate:1.5});
  assert.equal(restored.minutes.id,'active');assert.equal(restored.position,12);assert.equal(restored.track,'');
  assert.equal(resolveMeetingView(meeting,{minutesId:'old'}).minutes.id,'old'); // Historical choice remains viewable.
  assert.equal(resolveMeetingView({...meeting,artifacts:[]},{}).workspace,'transcript');
});
test('corrupt or blocked browser storage never prevents opening meetings',()=>{
  const bad={getItem:()=>{throw Error('denied')},setItem:()=>{throw Error('denied')}};
  assert.deepEqual(readMeetingView('a',bad),{});assert.equal(saveMeetingView('a',{position:2},bad),false);assert.equal(lastMeeting(bad),'');
  const storage=memory();storage.setItem('tingji.meeting.views.v1',JSON.stringify({a:{workspace:'delete',position:-1,track:'remote',rate:20,minutesId:5}}));
  assert.deepEqual(readMeetingView('a',storage),{workspace:undefined,minutesId:undefined,position:0,track:'',rate:1});
});
test('bookmarks stay bounded to the last 100 meetings',()=>{
  const storage=memory();for(let i=0;i<110;i++)saveMeetingView('m'+i,{position:i},storage);
  assert.equal(Object.keys(JSON.parse(storage.getItem('tingji.meeting.views.v1'))).length,100);
  assert.deepEqual(readMeetingView('m0',storage),{});assert.equal(readMeetingView('m109',storage).position,109);
});
test('personal and deferred records do not drive delivery; meeting-intended stale records still do',()=>{
 const notes=[{id:'personal',note_scope:'personal'},{id:'meeting',note_scope:'meeting'},{id:'defer',note_scope:'meeting'},{id:'approved',note_scope:'personal'},{id:'retained',note_scope:'meeting'}];
 const flow={attention:[{note_id:'defer',state:'deferred',review:{decision:'deferred'}},{note_id:'approved',state:'stale',review:{decision:'meeting'}},{note_id:'retained',state:'stale',review:{decision:'personal'}}]};
 assert.equal(deliveryAttentionCount({notes,flow}),2);
 assert.equal(deliveryAttentionCount({notes:[notes[0]],flow}),0);
});
