import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/transcript/paragraphs.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {paragraphs}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const parts=[{id:'a',start:0,end:25,text:'这个功能计划在下周',speaker:'发言人1',continuation_id:'flow',continues_next:true},
{id:'b',start:25,end:30,text:'完成验证，然后发布。',speaker:'发言人1',continuation_id:'flow',continues_previous:true,continues_next:false}];
test('Continuous audio reads as one paragraph without mutating source segments or timestamps',()=>{
 const saved=structuredClone(parts),result=paragraphs(parts);
 assert.equal(result.length,1);assert.equal(result[0].text,'这个功能计划在下周完成验证，然后发布。');
 assert.deepEqual(result[0].parts,parts);assert.deepEqual(parts,saved);assert.equal(result[0].end,30);
});
test('Speaker changes, gaps, explicit pauses and different groups break paragraph linking',()=>{
 for(const update of [{speaker:'发言人2'},{start:26},{continues_previous:false},{continuation_id:'other'}])
  assert.equal(paragraphs([parts[0],{...parts[1],...update}]).length,2);
 assert.equal(paragraphs(parts.map(p=>({...p,continuation_id:undefined}))).length,2);
});
test('English word boundaries stay readable without removing or inventing punctuation',()=>{
 const result=paragraphs([{...parts[0],text:'review the'},{...parts[1],text:'release plan.'}]);
 assert.equal(result[0].text,'review the release plan.');
});
test('A confirmed same-track forced cut can join a dependent Chinese phrase without editing evidence',()=>{
 const left={...parts[0],text:'GDP增速是负。',source_track:'system',boundary_reason:'hard_limit'};
 const right={...parts[1],text:'的百分之二十八点八。',source_track:'system'};
 const [group]=paragraphs([left,right]);
 assert.equal(group.text,'GDP增速是负的百分之二十八点八。');
 assert.equal(group.parts[0].text,'GDP增速是负。');
 assert.equal(paragraphs([left,{...right,source_track:'microphone'}]).length,2);
});
