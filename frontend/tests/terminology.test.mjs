import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({entryPoints:[new URL('../src/features/terminology/terms.ts',import.meta.url).pathname],bundle:true,format:'esm',write:false});
const {searchForms}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const term={name:'Kubernetes',aliases:['库伯内特斯'],status:'confirmed'};
test('Confirmed terminology expands in both directions with case-insensitive exact aliases',()=>{
 assert.deepEqual(searchForms(' KUBERNETES ',[term]),['kubernetes','库伯内特斯']);
 assert.deepEqual(searchForms('库伯内特斯',[term]),['库伯内特斯','kubernetes']);
 assert.equal(searchForms('kuber',[term]).length,3);
});
test('Candidates, ignored and stale terms never expand searches',()=>{
 for(const update of [{status:'candidate'},{status:'dismissed'},{stale:true}])
  assert.deepEqual(searchForms('Kubernetes',[{...term,...update}]),['kubernetes']);
 assert.deepEqual(searchForms('',[term]),[]);
 assert.deepEqual(searchForms('k',[term]),['k']);
});
test('Alias expansion leaves source term objects unchanged and avoids duplicates',()=>{
 const terms=[term,{...term,aliases:['库伯内特斯','Kubernetes']}],before=structuredClone(terms);
 assert.deepEqual(searchForms('Kubernetes',terms),['kubernetes','库伯内特斯']);
 assert.deepEqual(terms,before);
});
