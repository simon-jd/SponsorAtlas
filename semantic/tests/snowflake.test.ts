import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {generateKeyPairSync} from 'node:crypto';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const require=createRequire(import.meta.url);

test('Snowflake source fails closed and uses key-pair read-only configuration',async()=>{
 const {sourceSettings,ReadOnlySnowflakeDriver}=require('../source.cjs');
 const tmp=mkdtempSync(path.join(os.tmpdir(),'semantic-snowflake-'));
 try{
  const {privateKey}=generateKeyPairSync('rsa',{modulusLength:2048});
  const key=path.join(tmp,'key.pem');writeFileSync(key,privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600});
  const now=Date.parse('2026-10-24T12:00:00Z');
  const base={SEMANTIC_SOURCE:'snowflake',SEMANTIC_SNOWFLAKE_OPT_IN:'1',SNOWFLAKE_ACCOUNT:'test-only-unconnected',SNOWFLAKE_PRIVATE_KEY_FILE:key,SNOWFLAKE_TRIAL_STARTED_AT:'2026-10-20T00:00:00Z',SNOWFLAKE_TRIAL_EXPIRES_AT:'2026-11-19T00:00:00Z'};
  const settings=sourceSettings(base,now);
  assert.equal(settings.source,'snowflake');assert.equal(settings.config.username,'CUBE_SVC');assert.equal(settings.config.role,'ANALYST');assert.equal(settings.config.warehouse,'SPONSORATLAS_XS');assert.equal(settings.config.authenticator,'SNOWFLAKE_JWT');assert.equal(settings.config.readOnly,true);
  const driver=new ReadOnlySnowflakeDriver(settings,()=>now);
  assert.equal(driver.readOnly(),true);
  const options=await driver.prepareConnectOptions();assert.equal(options.authenticator,'SNOWFLAKE_JWT');assert.equal(options.role,'ANALYST');assert.equal(options.password,undefined);
  for(const sql of ['CREATE TABLE x(a int)','INSERT INTO x VALUES(1)','SELECT * FROM RAW.LCA.x','SELECT * FROM ANALYTICS.MARTS.fact_my_applications','SELECT 1; DROP TABLE x'])await assert.rejects(driver.query(sql,[]));
  for(const patch of [{SEMANTIC_SNOWFLAKE_OPT_IN:''},{SNOWFLAKE_ACCOUNT:''},{SNOWFLAKE_PRIVATE_KEY_FILE:''},{SNOWFLAKE_ROLE:'TRANSFORMER'},{SNOWFLAKE_USER:'DBT_SVC'},{SNOWFLAKE_WAREHOUSE:'OTHER'},{SNOWFLAKE_PASSWORD:'forbidden'},{CUBEJS_DB_PASS:'forbidden'},{SNOWFLAKE_TRIAL_STARTED_AT:''},{SNOWFLAKE_TRIAL_EXPIRES_AT:''},{SNOWFLAKE_TRIAL_EXPIRES_AT:'2026-10-23T00:00:00Z'},{SNOWFLAKE_TRIAL_EXPIRES_AT:'2027-01-01T00:00:00Z'}])assert.throws(()=>sourceSettings({...base,...patch},now),undefined,JSON.stringify(Object.keys(patch)));
  assert.throws(()=>sourceSettings({...base,SEMANTIC_SOURCE:''},now));
  for(const method of ['downloadQueryResults','memory','stream'])await assert.rejects(driver[method]('CREATE TABLE forbidden(a int)',[],{}));
  const expired=new ReadOnlySnowflakeDriver(settings,()=>settings.cutoff);
  for(const method of ['query','downloadQueryResults','memory','stream'])await assert.rejects(expired[method]('SELECT 1',[],{}),/cutoff/);
  await expired.release();
  const startup=[];
  for(const [patch,reason] of [
   [{SEMANTIC_SOURCE:''},/SEMANTIC_SOURCE must explicitly/],
   [{SEMANTIC_SNOWFLAKE_OPT_IN:''},/Explicit SEMANTIC_SNOWFLAKE_OPT_IN/],
   [{SNOWFLAKE_ROLE:'TRANSFORMER'},/Invalid SNOWFLAKE_ROLE/],
   [{SNOWFLAKE_PASSWORD:'forbidden'},/Password authentication is forbidden/],
   [{SNOWFLAKE_PRIVATE_KEY_FILE:''},/account and RSA private key file required/],
   [{SNOWFLAKE_TRIAL_EXPIRES_AT:'2026-10-23T00:00:00Z'},/Trial inactive/]
  ] as const){
   const result=spawnSync(process.execPath,['server.cjs'],{cwd:path.resolve(import.meta.dirname,'..'),env:{PATH:process.env.PATH,...base,...patch},encoding:'utf8',timeout:10000});assert.equal(result.status,1,result.stderr);assert.match(result.stderr,reason);assert.doesNotMatch(result.stdout,/listening/);startup.push({invalid_field:Object.keys(patch)[0],status:result.status,target_error:result.stderr.trim()});
  }
  const {mkdirSync}=require('node:fs');mkdirSync('../.tools/semantic/scope-completion',{recursive:true});writeFileSync('../.tools/semantic/scope-completion/negative-startup.json',JSON.stringify({kind:'OFFLINE_CONFIG_NEGATIVES_NO_CONNECTION',startup},null,2));
  await driver.release();
 }finally{rmSync(tmp,{recursive:true,force:true});}
});

test('real Cube Snowflake compiler uses shared marts projections and isolated external rollups',async()=>{
 const {modelRepository,assertReadQuery}=require('../source.cjs');
 const {CubejsServer}=require('@cubejs-backend/server');
 const old=process.env.CUBEJS_DB_TYPE;process.env.CUBEJS_DEV_MODE='false';process.env.CUBEJS_DB_TYPE='snowflake';process.env.CUBEJS_TELEMETRY='false';
 const cube=new CubejsServer({apiSecret:'compile-only-not-a-server',devServer:false,scheduledRefreshTimer:false,repositoryFactory:()=>modelRepository('snowflake'),externalDbType:'duckdb',driverFactory:()=>({type:'snowflake'}),logger:()=>{}});
 try{
  const compiler=await cube.core.getCompilerApi({});
  const groups=['employer','soc_major','soc','metro','fiscal_year'];
  const requests=[
   {measures:['lca.case_count','lca.certified_rate','lca.wage_p50','lca.wage_p75','lca.wage_p90']},
   {measures:['postings.count']}, {measures:['wages.oews_median']},
   {measures:['lca.case_count','lca.certified_rate'],dimensions:['lca.fiscal_year']},
   {measures:['lca.case_count'],dimensions:groups.map(d=>'lca.'+d),filters:groups.map(d=>({member:'lca.'+d,operator:'equals',values:[d==='fiscal_year'?'2025':'fixture-code']}))},
   {measures:['lca_metrics.case_count','lca_metrics.wage_p50']},
   {measures:['posting_metrics.count']}, {measures:['wage_metrics.oews_median']}
  ];
  const evidence=[];
  for(const request of requests){
   const sql=await compiler.getSql({dimensions:[],timeDimensions:[],filters:[],timezone:'UTC',limit:10000,order:[],...request});
   const sourceSql=sql.preAggregations[0]?.sql[0]||sql.sql[0];
   assert.match(sourceSql,/ANALYTICS\.MARTS\./i);assert.doesNotMatch(sourceSql,/read_parquet|mart_lca|CREATE TABLE|RAW\.|fact_my_applications|dim_employer/i);assertReadQuery(sourceSql);
   if(request.measures.some(m=>/wage_p[579]/.test(m)))assert.deepEqual(sql.preAggregations,[]);
   for(const p of sql.preAggregations){assert.equal(p.external,true);assert.match(p.sql[0],/^SELECT/i);assertReadQuery(p.sql[0]);}
   for(const [query,,options] of sql.cacheKeyQueries||[])if(!options?.external)assertReadQuery(query);
   evidence.push({request,sql:sql.sql,preAggregations:sql.preAggregations,cacheKeyQueries:sql.cacheKeyQueries});
  }
  const {writeFileSync,mkdirSync}=require('node:fs');mkdirSync('../.tools/semantic/scope-completion',{recursive:true});writeFileSync('../.tools/semantic/scope-completion/snowflake-compiled.json',JSON.stringify({kind:'COMPILE_ONLY_NO_SNOWFLAKE_CONNECTION',evidence},null,2));
 }finally{await cube.core.shutdown();if(old===undefined)delete process.env.CUBEJS_DB_TYPE;else process.env.CUBEJS_DB_TYPE=old;}
});
