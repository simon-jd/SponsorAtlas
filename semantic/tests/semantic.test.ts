import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFileSync, mkdirSync, openSync, closeSync, rmSync } from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const jwt = require('jsonwebtoken');
const root = path.resolve(import.meta.dirname, '../..');
const kind = process.env.SEMANTIC_KIND || 'fixture';
const work = path.join(root, '.tools/semantic', kind);
mkdirSync(work,{recursive:true});
// Own disposable scratch only: every run proves a cold preaggregation build.
rmSync(path.join(work,'scratch'),{recursive:true,force:true});
const secret = 'test-only-ephemeral-' + crypto.randomUUID();
const token = jwt.sign({sub:'semantic-test'},secret,{expiresIn:'10m'});
const port = kind === 'fixture' ? 4317 : 4318;
const log = openSync(path.join(work,'cube.log'),'w');
const service = spawn(process.execPath,['--max-old-space-size=1024','server.cjs'],{
 cwd:path.join(root,'semantic'),env:{...process.env,SEMANTIC_SOURCE:'parquet',SEMANTIC_WORK:work,CUBEJS_API_SECRET:secret,PORT:String(port)},stdio:['ignore',log,log]
});
const records:any[]=[];
async function api(route:string,query?:any,auth=true) {
 const response=await fetch(`http://127.0.0.1:${port}/cubejs-api/v1/${route}`,{method:query?'POST':'GET',headers:{...(auth?{Authorization:token}:{}),'Content-Type':'application/json'},...(query?{body:JSON.stringify({query})}:{})});
 const body=await response.json();
 return {status:response.status,body};
}
async function load(query:any) {
 for(let i=0;i<80;i++) {
  const r=await api('load',query);
  if(r.body.error==='Continue wait') {await new Promise(r=>setTimeout(r,100));continue;}
  records.push({query,...r});
  assert.equal(r.status,200,JSON.stringify(r.body));
  assert.ok(!r.body.error,JSON.stringify(r.body)); return r.body;
 }
 throw Error('Cube query timeout');
}
function n(row:any,key:string,expected:number|null) { if(expected===null) assert.equal(row[key],null);else assert.ok(Math.abs(Number(row[key])-expected)<1e-6,`${key}: ${row[key]} != ${expected}`); }
const dims=['employer','soc_major','soc','metro','fiscal_year'];
const metrics=['case_count','certified_rate','wage_p50','wage_p75','wage_p90'];
const filters=(member:string,...values:string[])=>[{member,operator:'equals',values}];

test('real Cube REST semantic contract',async(t)=>{
 let booted=false;
 try {
  let ready=false;
  for(let i=0;i<150;i++) {
   if(service.exitCode!==null) throw Error('Cube exited: '+readFileSync(path.join(work,'cube.log'),'utf8').slice(-4000));
   try{const r=await api('meta');if(r.status===200){ready=true;break;}}catch{}
   await new Promise(r=>setTimeout(r,100));
  }
  assert.ok(ready,'Cube readiness');booted=true;
  await t.test('requires JWT',async()=>assert.equal((await api('meta',undefined,false)).status,403));
  await t.test('SQL and extra API routes disabled',async()=>{
   assert.equal((await api('sql',{measures:['lca.case_count']})).status,404);
   assert.equal((await api('cubesql',{query:'select 1'})).status,404);
   assert.equal((await api('load',{measures:['lca.case_count','postings.count']})).status,400);
  });
  await t.test('bounded engine refuses source writes and external reads',async()=>{
   const d=JSON.parse(readFileSync(path.join(work,'driver-evidence.json'),'utf8'));
   assert.equal(d.settings[0].threads,'1');assert.equal(d.settings[0].external_access,false);
   assert.match(d.source_overwrite,/Permission|denied/);assert.match(d.source_os_write,/EACCES/);assert.match(d.external_read,/Permission|denied/);
  });
  await t.test('all seven metrics present in actual Cube meta',async()=>{
   const meta=(await api('meta')).body;
   for(const [cube,names] of [['lca',metrics],['postings',['count']],['wages',['oews_median']]] as const) {
    const c=meta.cubes.find((c:any)=>c.name===cube); assert.ok(c,`${cube} missing`);
    for(const name of names) assert.ok(c.measures.some((m:any)=>m.name===`${cube}.${name}`));
   }
  });
  const oracle=JSON.parse(readFileSync(path.join(work,'oracle.json'),'utf8'));
  await t.test('LCA five metrics match independent SQL and hand fixture',async()=>{
   const r=await load({measures:metrics.map(m=>'lca.'+m)});
   for(const m of metrics)n(r.data[0],'lca.'+m,oracle.lca[m]);
   if(kind==='fixture') assert.deepEqual(oracle.lca,{case_count:6,certified_count:3,certified_rate:0.5,wage_p50:30,wage_p75:40,wage_p90:76});
   assert.deepEqual(r.usedPreAggregations||{},{},'exact percentiles must read full fact distribution');
  });
  await t.test('postings and OEWS match independent SQL',async()=>{
   const postings=await load({measures:['postings.count']});
   n(postings.data[0],'postings.count',oracle.postings);
   assert.ok(Object.keys(postings.usedPreAggregations||{}).some(k=>k.includes('counts')));
   const r=await load({measures:['wages.oews_median'],filters:oracle.wage_filters});
   n(r.data[0],'wages.oews_median',oracle.oews_median);
  });
  await t.test('five dimensions grouped together preserve every LCA row',async()=>{
   const rows:any[]=[];
   // Page until a short page; the bound only stops a runaway loop (full FY2025: 280,799 groups).
   for(let offset=0;offset<=oracle.lca_groups.count;offset+=50000){
    const r=await load({measures:['lca.case_count'],dimensions:dims.map(d=>'lca.'+d),limit:50000,offset,order:Object.fromEntries(dims.map(d=>['lca.'+d,'asc']))});
    rows.push(...r.data);if(r.data.length<50000)break;
   }
   n({'sum':rows.reduce((s:number,x:any)=>s+Number(x['lca.case_count']),0)},'sum',oracle.lca.case_count);
   for(const d of dims) assert.ok(Object.hasOwn(rows[0],'lca.'+d));
   const canon=rows.map(r=>[...dims.map(d=>String(r['lca.'+d])),String(r['lca.case_count'])].join('\u001f')).sort().join('\n');
   assert.equal(rows.length,oracle.lca_groups.count);
   assert.equal(createHash('sha256').update(canon).digest('hex'),oracle.lca_groups.sha256);

  });
  await t.test('views execute and additive preaggregation is built and used',async()=>{
   const r=await load({measures:['lca.case_count','lca.certified_rate'],dimensions:['lca.fiscal_year']});
   assert.ok(Object.keys(r.usedPreAggregations||{}).some(k=>k.includes('counts')),'rollup not used');
   for(const expected of oracle.fiscal_groups){const row=r.data.find((x:any)=>Number(x['lca.fiscal_year'])===expected.fiscal_year);assert.ok(row);n(row,'lca.case_count',expected.case_count);n(row,'lca.certified_rate',expected.certified_rate);}
   const v=await load({measures:['lca_metrics.case_count','lca_metrics.wage_p50']});
   n(v.data[0],'lca_metrics.case_count',oracle.lca.case_count);n(v.data[0],'lca_metrics.wage_p50',oracle.lca.wage_p50);
   n((await load({measures:['posting_metrics.count']})).data[0],'posting_metrics.count',oracle.postings);
   n((await load({measures:['wage_metrics.oews_median'],filters:oracle.wage_filters.map((f:any)=>({...f,member:f.member.replace('wages.','wage_metrics.')}))})).data[0],'wage_metrics.oews_median',oracle.oews_median);
  });
  if(kind==='fixture'){
   await t.test('combined filters, unknown members, denominator and null wages',async()=>{
    const r=await load({measures:metrics.map(m=>'lca.'+m),filters:[...filters('lca.employer','A'),...filters('lca.soc','15-1252'),...filters('lca.soc_major','15-0000'),...filters('lca.metro','M1'),...filters('lca.fiscal_year','2025')]});
    for(const [m,v] of Object.entries({case_count:2,certified_rate:0.5,wage_p50:15,wage_p75:17.5,wage_p90:19}))n(r.data[0],'lca.'+m,v);
    n((await load({measures:['lca.case_count'],filters:filters('lca.employer','UNKNOWN')})).data[0],'lca.case_count',1);
    const empty=await load({measures:metrics.map(m=>'lca.'+m),filters:filters('lca.employer','ABSENT')});
    n(empty.data[0],'lca.case_count',0);for(const m of metrics.slice(1))n(empty.data[0],'lca.'+m,null);
    const nullWage=await load({measures:['lca.case_count','lca.wage_p50'],filters:filters('lca.employer','UNKNOWN')});n(nullWage.data[0],'lca.case_count',1);n(nullWage.data[0],'lca.wage_p50',null);
    const pg=await load({measures:['postings.count'],dimensions:dims.map(d=>'postings.'+d)});assert.equal(pg.data.reduce((s:number,r:any)=>s+Number(r['postings.count']),0),4);
   });
   await t.test('OEWS cannot average benchmarks; major uses official major row; suppression explicit',async()=>{
    n((await load({measures:['wages.oews_median']})).data[0],'wages.oews_median',null);
    n((await load({measures:['wages.oews_median'],filters:[...filters('wages.soc_major','15-0000'),...filters('wages.occupation_level','major'),...filters('wages.area','1:99'),...filters('wages.survey_year','2025')]})).data[0],'wages.oews_median',70);
    const r=await load({measures:['wages.oews_median'],dimensions:['wages.wage_status','wages.median_raw'],filters:filters('wages.soc','29-9999')});n(r.data[0],'wages.oews_median',null);assert.equal(r.data[0]['wages.median_raw'],'*');
    n((await load({measures:['wages.oews_median'],filters:filters('wages.soc','15-1252')})).data[0],'wages.oews_median',null);
   });
  }
 }finally{
  const {writeFileSync}=await import('node:fs');writeFileSync(path.join(work,'rest-evidence.json'),JSON.stringify({kind,records},null,2));
  service.kill('SIGTERM');
  const killed=setTimeout(()=>service.kill('SIGKILL'),5000);
  await new Promise<void>(resolve=>{if(service.exitCode!==null)resolve();else service.once('exit',()=>resolve());});clearTimeout(killed);closeSync(log);
  if(booted)assert.equal(service.exitCode,0,'Cube must shut down cleanly');
 }
});
