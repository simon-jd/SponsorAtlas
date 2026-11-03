import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import crypto from 'node:crypto';
const require=createRequire(import.meta.url);
const root=path.resolve(import.meta.dirname,'../..');
test('fallback requires a real native seven-metric validation record and exact file fingerprints',()=>{
 const work=fs.mkdtempSync(path.join(os.tmpdir(),'semantic-fallback-fixture-'));
 try{
  // Synthetic files only; the production-kind marker below is exercised as an untrusted input.
  const original=JSON.parse(fs.readFileSync(path.join(root,'.tools/semantic/fixture/export-evidence.json'),'utf8'));
  const manifest={...original,kind:'ACTUAL_SNOWFLAKE_PUBLIC_MARTS',test_only:'SYNTHETIC_GATE_PROBE_NOT_AN_ACTUAL_EXPORT',exports:original.exports.map((x:any)=>({...x,path:path.join(work,path.basename(x.path))}))};
  for(const item of manifest.exports){fs.copyFileSync(path.join(root,'.tools/semantic/fixture',path.basename(item.path)),item.path);fs.chmodSync(item.path,0o444);}
  const write=()=>fs.writeFileSync(path.join(work,'export-evidence.json'),JSON.stringify(manifest));write();
  const load=()=>spawnSync(process.execPath,['-e',"require('./parquet.cjs')"],{cwd:path.join(root,'semantic'),env:{PATH:process.env.PATH,SEMANTIC_WORK:work,CUBEJS_API_SECRET:'fixture-gate-only'},encoding:'utf8',timeout:10000});
  const missing=load();assert.equal(missing.status,1);assert.match(missing.stderr,/must pass native validation/);
  const validation=spawnSync(process.execPath,['scripts/validate_snapshot.cjs',work],{cwd:path.join(root,'semantic'),encoding:'utf8',timeout:10000});assert.equal(validation.status,0,validation.stderr);
  manifest.validation=JSON.parse(validation.stdout);write();assert.equal(load().status,0);
  manifest.validation.checksums.lca='tampered-record';write();const tampered=load();assert.equal(tampered.status,1);assert.match(tampered.stderr,/Validated snapshot fingerprint mismatch/);
 }finally{fs.rmSync(work,{recursive:true,force:true});}
});
test('native snapshot entry rejects null/empty/missing numeric oracle values for zero metrics',async()=>{
 const work=fs.mkdtempSync(path.join(os.tmpdir(),'semantic-zero-snapshot-'));
 const {DuckDBDriver}=require('@cubejs-backend/duckdb-driver');
 const driver=new DuckDBDriver({databasePath:':memory:',initSql:"SET threads=1; SET memory_limit='128MB'; SET max_temp_directory_size='64MB';"});
 try{
  const original=JSON.parse(fs.readFileSync(path.join(root,'.tools/semantic/fixture/export-evidence.json'),'utf8'));
  const manifest={...original,kind:'ANALYTICS_TEST',test_only:'SYNTHETIC_EMPTY_SNAPSHOT_STRICT_NUMBER_PROBE',oracle:{lca:{case_count:0,certified_rate:null,wage_p50:null,wage_p75:null,wage_p90:null},postings:0,oews_median:null},exports:[] as any[]};
  const quote=(s:string)=>"'"+s.replaceAll("'","''")+"'";
  for(const name of ['lca','postings','wages']){
   const file=path.join(work,name+'.parquet');
   await driver.query(`COPY (SELECT * FROM read_parquet(${quote(path.join(root,'.tools/semantic/fixture',name+'.parquet'))}) WHERE false) TO ${quote(file)} (FORMAT PARQUET)`,[]);
   fs.chmodSync(file,0o444);manifest.exports.push({path:file,rows:0,size:fs.statSync(file).size,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')});
  }
  const validate=()=>{fs.writeFileSync(path.join(work,'export-evidence.json'),JSON.stringify(manifest));return spawnSync(process.execPath,[process.env.SEMANTIC_TEST_VALIDATOR||'scripts/validate_snapshot.cjs',work],{cwd:path.join(root,'semantic'),encoding:'utf8',timeout:10000});};
  const valid=validate();assert.equal(valid.status,0,valid.stderr);assert.equal(JSON.parse(valid.stdout).all_seven_match,true);
  for(const bad of [null,'','invalid',undefined]){(manifest.oracle as any).postings=bad;const result=validate();assert.equal(result.status,1,`Invalid zero oracle ${JSON.stringify(bad)} was accepted`);assert.match(result.stderr,/Native snapshot verification failed/);}
  (manifest.oracle as any).postings='0';assert.equal(validate().status,0);
 }finally{await driver.release();fs.rmSync(work,{recursive:true,force:true});}
});
