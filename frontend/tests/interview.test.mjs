import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const bundle=await build({entryPoints:[new URL('../src/features/minutes/InterviewMinutesBody.tsx',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {projectInterview}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
const evidence=[{segment_id:'a',quote:'我负责接口'}];
const concern={content:'基线待核对',reason:'缺少测量方法',followup:'请说明测量方法',evidence};
const doc={title:'合成面试',interview:{background:[],qa:[{question:'本人职责？',content:'候选人表示负责接口',evidence}],competencies:[{dimension:'项目经历',coverage:'covered',content:'已获得具体回答',evidence}],concerns:[concern],followups:[],summary:[]},interview_reviews:{},interviewer_evaluation:'人工判断',interview_context:{candidate:'候选人代称',role:'后端岗位'}};
test('Interview exports contain only confirmed concerns and preserve candidate attribution',()=>{
 const pending=projectInterview(doc);assert.equal(pending.open_questions.length,0);
 assert.match(pending.conclusions[0].content,/候选人表示/);
 const kept=projectInterview({...doc,interview_reviews:{0:'kept'}});assert.match(kept.open_questions[0].content,/建议追问：请说明测量方法/);
 assert.equal(projectInterview({...doc,interview_reviews:{0:'ignored'}}).open_questions.length,0);
 assert.deepEqual(doc.interview_reviews,{});
});
test('Preparation is labeled as human input and unreviewed evaluation stays outside export',()=>{
 const ready=projectInterview(doc);assert.match(ready.overview[0].content,/面试准备（人工填写）/);
 assert.match(ready.summary[0].content,/面试官评价（人工填写）：人工判断/);
 const stale=projectInterview({...doc,interview_evaluation_needs_review:true});assert.equal(stale.summary.length,0);assert.equal(stale.interviewer_evaluation,'人工判断');
});
