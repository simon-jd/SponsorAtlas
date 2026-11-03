import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {validate,boundedJSON,pageScope,type Envelope} from '../lib/vitals/schema.ts';
import {reporter} from '../lib/vitals/client.ts';
import {empty,merge,store,localAdapter,blobAdapter,configuredAdapter,aggregate,MAX_RECORDS,RETENTION_MS} from '../lib/vitals/store.ts';
const event=():Envelope=>({v:1,navigation:'navigation-123456789',page:'search',locale:'en',device:'wide',navigationType:'document',metrics:[{id:'metric-1',name:'LCP',value:2100,sequence:1}]});
test('scalar allowlist rejects URLs, unknown keys, invalid values and oversized streaming body',async()=>{
 assert.equal(pageScope('/en/employers/private-id')?.page,'employer');assert.equal(pageScope('/en?secret=x'),null);
 assert.deepEqual(validate(event()),event());
 const enumFields=[['page',['search','employer','compare','map']],['locale',['en','zh']],['device',['narrow','wide']],['navigationType',['document','bfcache']]] as const;
 for(const [field,allowed] of enumFields){
  for(const legal of allowed){const input={...event(),[field]:legal};assert.equal(validate(input)[field],legal,`${field} accepts ${legal}`);}
  for(const legal of allowed){
   const invalid:[string,unknown][]=[['single-element array',[legal]],['nested array',[[legal]]],['null',null],['false',false],['number',1],['object',{}],['undefined',undefined]];
   for(const [label,value] of invalid){
    const input={...event(),[field]:value};assert.throws(()=>validate(input),/schema/,`${field} rejects ${label} for ${legal}`);
   }
   const {[field]:_omitted,...missing}=event();assert.throws(()=>validate(missing),/schema/,`${field} rejects missing value for ${legal}`);
  }
 }
 for(const v of [{...event(),url:'secret'},{...event(),metrics:[]},{...event(),metrics:[{...event().metrics[0],value:NaN}]},{...event(),metrics:[{...event().metrics[0],value:-1}]},{...event(),metrics:[{...event().metrics[0],entries:[]}]}])assert.throws(()=>validate(v));
 await assert.rejects(boundedJSON(new Request('http://localhost',{method:'POST',body:'x'.repeat(2049)})));
});
test('document metric registration helper preserves scope, sequences, BFcache and omits softnav/absent INP',()=>{
 const output:Envelope[]=[];let n=0;const report=reporter('/zh/map','wide',e=>output.push(e),()=>`navigation-1234567-${++n}`);
 const m={id:'a',name:'LCP',value:100,navigationType:'navigate',navigationId:1};report(m);report({...m,value:200});report.restore('/zh/map');report({...m,id:'b',navigationId:1,navigationType:'back-forward-cache'});report({...m,id:'c',navigationType:'soft-navigation'});
 assert.equal(output.length,3);assert.equal(output[0].navigation,output[1].navigation);assert.equal(output[1].metrics[0].sequence,2);assert.notEqual(output[2].navigation,output[0].navigation);assert.equal(output[2].navigationType,'bfcache');assert.ok(output.every(x=>x.page==='map'&&x.locale==='zh'&&x.metrics[0].name==='LCP'));
});
test('latest metric value deduplicates by navigation/name; reordered retry cannot overwrite; scope immutable',()=>{
 const e=event(),s=merge(empty(),e,1000);e.metrics[0]={...e.metrics[0],value:2300,sequence:2};const updated=merge(s,e,1001);assert.equal(updated.rows.length,1);assert.equal(updated.rows[0].value,2300);
 assert.equal(merge(updated,event(),1002).rows[0].value,2300);assert.throws(()=>merge(updated,{...e,page:'map'},1003));assert.throws(()=>merge(updated,{...e,metrics:[{...e.metrics[0],id:'different'}]},1003));
 assert.deepEqual(aggregate(updated.rows,1002),{'search/en/wide/document/LCP':{n:1,p75:2300}});
});
test('retention, hard quota and global minute rate stay bounded',()=>{
 const s=merge(empty(),event(),1);assert.equal(merge(s,{...event(),navigation:'navigation-987654321'},RETENTION_MS+2).rows.length,1);
 assert.throws(()=>merge({...s,minute:0,requests:60},event(),2),/rate/);
 assert.throws(()=>merge({...s,rows:Array.from({length:MAX_RECORDS},(_,i)=>({...s.rows[0],navigation:`other-${i}`}))},event(),2),/quota/);
});
test('local QA sink persists final dedup state across adapter instances; production misconfiguration fails closed',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'sponsoratlas-rum-'));
 try{await Promise.all([store(event(),localAdapter(dir),1000),store({...event(),metrics:[{...event().metrics[0],value:2600,sequence:2}]},localAdapter(dir),1001)]);const saved=JSON.parse(await readFile(join(dir,'vitals.json'),'utf8'));assert.equal(saved.rows.length,1);assert.equal(saved.rows[0].value,2600);assert.throws(()=>configuredAdapter({RUM_STORAGE:'local',RUM_LOCAL_DIR:dir}));assert.throws(()=>configuredAdapter({}));}finally{await rm(dir,{recursive:true});}
});
test('Blob adapter uses private HTTPS path, ETag and bounded retry; no fallback',async()=>{
 assert.throws(()=>blobAdapter('http://localhost/vitals/vitals.json?sig=x'));
 const original=globalThis.fetch;const calls:{method:string;headers:Headers}[]=[];let puts=0;
 globalThis.fetch=async(_url,init)=>{calls.push({method:init?.method||'GET',headers:new Headers(init?.headers)});if(init?.method==='PUT')return new Response(null,{status:++puts===1?412:201});return new Response(JSON.stringify(empty()),{headers:{etag:'"version1"'}});};
 try{await store(event(),blobAdapter('https://example.blob.core.windows.net/vitals/vitals.json?sig=TEST_ONLY'),1000);assert.equal(puts,2);assert.equal(calls[1].headers.get('if-match'),'"version1"');assert.equal(calls[1].headers.get('x-ms-blob-type'),'BlockBlob');}finally{globalThis.fetch=original;}
});


test('canonical origin fails closed for absent/invalid production settings and restricts HTTP to explicit loopback QA',async()=>{
 const {rumOrigin}=await import('../lib/vitals/origin.ts');
 assert.equal(rumOrigin({RUM_ORIGIN:'https://sponsoratlas.example'}),'https://sponsoratlas.example');
 assert.equal(rumOrigin({RUM_ORIGIN:'http://127.0.0.1:3100',RUM_QA_ONLY:'1'}),'http://127.0.0.1:3100');
 for(const env of [{},{RUM_ORIGIN:'http://sponsoratlas.example'},{RUM_ORIGIN:'http://127.0.0.1:3100'},{RUM_ORIGIN:'https://site.example/path'},{RUM_ORIGIN:'https://site.example?token=x'},{RUM_ORIGIN:'https://user:secret@site.example'},{RUM_ORIGIN:'http://localhost:3100',RUM_QA_ONLY:'1'},{RUM_ORIGIN:'ftp://127.0.0.1',RUM_QA_ONLY:'1'}])assert.throws(()=>rumOrigin(env));
});
test('p75 nearest-rank groups per metric and never fabricates absent INP',()=>{
 let state=empty();for(const [i,value] of [100,200,300,10000].entries())state=merge(state,{...event(),navigation:`navigation-sample-${i}`,metrics:[{...event().metrics[0],value}]},1000);
 assert.deepEqual(aggregate(state.rows,1001),{'search/en/wide/document/LCP':{n:4,p75:300}});
});
