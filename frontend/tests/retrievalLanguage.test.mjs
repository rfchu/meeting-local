import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/retrievalLanguage.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {readAnswerLanguage,saveAnswerLanguage,semanticLanguageHint}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
test('Answer language defaults safely and remembers an explicit language independently',()=>{
 let value=null;const storage={getItem:()=>value,setItem:(_,next)=>value=next};
 assert.equal(readAnswerLanguage(storage),'zh');
 saveAnswerLanguage('en',storage);assert.equal(readAnswerLanguage(storage),'en');
 saveAnswerLanguage('zh',storage);assert.equal(readAnswerLanguage(storage),'zh');
 value='auto';assert.equal(readAnswerLanguage(storage),'zh');
});
test('Blocked browser storage cannot prevent QA',()=>{
 const storage={getItem:()=>{throw Error('blocked')},setItem:()=>{throw Error('blocked')}};
 assert.equal(readAnswerLanguage(storage),'zh');
 assert.doesNotThrow(()=>saveAnswerLanguage('en',storage));
 assert.equal(readAnswerLanguage(),'zh');
});
test('Semantic guidance distinguishes Chinese builtin from unknown external scope',()=>{
 assert.match(semanticLanguageHint({semantic_scope:'zh',keyword_languages:['zh','en']}),/面向中文/);
 assert.match(semanticLanguageHint({semantic_scope:'model_dependent',keyword_languages:['zh','en']}),/不代表检索质量验证/);
 assert.match(semanticLanguageHint(),/关键词无需模型/);
});
