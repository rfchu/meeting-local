import test from 'node:test';import assert from 'node:assert/strict';import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/overview/notes.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {ensureNoteIds,toggleOverviewFocus,draftSourceId}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const personal={captured_at:12,note_type:'question',note_scope:'personal',raw_text:'确认截止时间'};
test('Note IDs survive adding, deleting, focus changes and session serialization without rewriting raw notes',()=>{
 let count=0;const create=()=>`note-${++count}`,input=[personal],before=structuredClone(input);
 const first=ensureNoteIds(input,create);assert.equal(first[0].client_id,'note-1');assert.deepEqual(input,before);
 const second=ensureNoteIds([...first,{...personal,captured_at:20}],create);assert.equal(second[0].client_id,'note-1');
 const restored=ensureNoteIds(JSON.parse(JSON.stringify(second)).slice(1),create);assert.equal(restored[0].client_id,'note-2');assert.equal(count,2);
});
test('Selecting personal notes for AI focus does not silently convert their meeting scope',()=>{
 const note={...personal,client_id:'stable'},selected=toggleOverviewFocus(note);
 assert.equal(selected.overview_focus,true);assert.equal(selected.note_scope,'personal');assert.equal(selected.client_id,'stable');assert.equal(selected.raw_text,note.raw_text);
 assert.equal(toggleOverviewFocus(selected).overview_focus,false);
 assert.deepEqual(toggleOverviewFocus({...note,raw_text:'',is_marker:true}),{...note,raw_text:'',is_marker:true});
});
test('Live source identities include zero indices and never fabricate IDs for provisional text',()=>{
 assert.equal(draftSourceId({live_range_id:'range',live_segment_index:0}),'range:0');
 assert.equal(draftSourceId({live_range_id:'range',live_segment_index:1}),'range:1');
 assert.equal(draftSourceId({text:'临时尾部'}),null);
});
