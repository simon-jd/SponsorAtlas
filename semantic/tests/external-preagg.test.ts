import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import http from 'node:http';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {DuckDBDriver}=require('@cubejs-backend/duckdb-driver');
const {DuckDBInstance}=require('@duckdb/node-api');
const {CubejsServer}=require('@cubejs-backend/server');
const express=require('express');
const jwt=require('jsonwebtoken');
const {modelRepository}=require('../source.cjs');
const {ExternalDuckDBDriver}=require('../external.cjs');
const root=path.resolve(import.meta.dirname,'../..');
const work=path.join(root,'.tools/semantic/readonly-external');
const sourceDb=path.join(work,'readonly-source.duckdb');
process.env.CUBEJS_TELEMETRY='false';process.env.CUBEJS_DEV_MODE='false';process.env.CUBEJS_DB_TYPE='duckdb';process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';process.env.CUBEJS_CACHE_AND_QUEUE_DRIVER='memory';
const sourceQueries:string[]=[],externalQueries:string[]=[];
const files=['lca','postings','wages'].map(n=>path.join(root,'.tools/semantic/fixture',n+'.parquet'));
function hash(file:string){return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
class PhysicalReadOnlyDriver extends DuckDBDriver{
 readOnly(){return true;}
 async init(){const instance=await DuckDBInstance.create(sourceDb,{access_mode:'READ_ONLY'});const defaultConnection=await instance.connect();await this.configureConnection(defaultConnection);return {instance,defaultConnection};}
 async query(sql:string,values:any[]=[],options?:any){sourceQueries.push(sql);if(!/^\s*SELECT\b/i.test(sql))throw Error('Read-only source refuses writes');return super.query(sql,values,options);}
}

test('true Cube cold-builds external rollups from a physical read-only source into a separate real DuckDB',async()=>{
 fs.mkdirSync(work,{recursive:true});
 for(const p of [sourceDb,path.join(work,'external-scratch')]){if(fs.existsSync(p))fs.chmodSync(p,0o700);fs.rmSync(p,{recursive:true,force:true});}
 const setup=new DuckDBDriver({databasePath:sourceDb,initSql:"SET threads=1; SET max_temp_directory_size='256MB';"});
 for(let i=0;i<files.length;i++)await setup.query(`CREATE VIEW mart_${['lca','postings','wages'][i]} AS SELECT * FROM read_parquet('${files[i]}')`,[]);
 await setup.release();fs.chmodSync(sourceDb,0o444);
 const before={source:hash(sourceDb),inputs:files.map(hash),size:fs.statSync(sourceDb).size,mtime_ns:fs.statSync(sourceDb,{bigint:true}).mtimeNs.toString()};
 const secret='mechanism-only-'+crypto.randomUUID();const token=jwt.sign({sub:'mechanism-fixture'},secret,{expiresIn:'5m'});
 const source=new PhysicalReadOnlyDriver({databasePath:sourceDb,initSql:`SET threads=1; SET max_temp_directory_size='256MB'; SET allowed_paths=[${files.map(x=>"'"+x+"'").join(',')}]; SET enable_external_access=false;`});
 const external=new ExternalDuckDBDriver(work,(sql:string)=>externalQueries.push(sql));
 const events:any[]=[];
 const cube=new CubejsServer({devServer:false,apiSecret:secret,scheduledRefreshTimer:false,repositoryFactory:()=>modelRepository('external-fixture'),driverFactory:()=>source,externalDbType:'duckdb',externalDriverFactory:()=>external,preAggregationsSchema:'semantic_preagg',orchestratorOptions:{queryCacheOptions:{queueOptions:{concurrency:1}},preAggregationsOptions:{externalRefresh:false,queueOptions:{concurrency:1}}},logger:(message:string)=>events.push(message)});
 const app=express();app.use(express.json());await cube.core.initApp(app);const server=http.createServer(app);
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));const port=(server.address() as any).port;
 const responses:any[]=[];let nativeWriteError;
 try{
  for(const query of [{measures:['lca.case_count','lca.certified_rate'],dimensions:['lca.fiscal_year']},{measures:['postings.count']},{measures:['lca.wage_p50','lca.wage_p75','lca.wage_p90']}]){
   const r=await fetch(`http://127.0.0.1:${port}/cubejs-api/v1/load`,{method:'POST',headers:{Authorization:token,'Content-Type':'application/json'},body:JSON.stringify({query})});const body=await r.json();responses.push({query,status:r.status,body});assert.equal(r.status,200,JSON.stringify(body));
  }
  assert.ok(responses[0].body.usedPreAggregations);assert.ok(responses[1].body.usedPreAggregations);assert.equal(responses[2].body.usedPreAggregations,undefined);
  assert.equal(responses[0].body.data.reduce((s:number,x:any)=>s+Number(x['lca.case_count']),0),6);assert.equal(Number(responses[1].body.data[0]['postings.count']),4);
  for(const [key,v] of Object.entries({wage_p50:30,wage_p75:40,wage_p90:76}))assert.equal(Number(responses[2].body.data[0]['lca.'+key]),v);
  assert.ok(events.includes('Downloading external pre-aggregation via query'));assert.ok(externalQueries.some(x=>/^CREATE TABLE/i.test(x)));assert.ok(externalQueries.some(x=>/^INSERT INTO/i.test(x)));
  assert.ok(sourceQueries.every(x=>/^\s*SELECT\b/i.test(x)));await assert.rejects(source.query('CREATE TABLE forbidden(a int)',[]));
  // Bypass the wrapper solely to prove the native source connection itself is read-only.
  const state=await source.getInitiatedState();await assert.rejects(state.defaultConnection.run('CREATE TABLE native_forbidden(a int)'),(error:any)=>{nativeWriteError=error.message;return /read.only|read-only/i.test(error.message);});
 }finally{await new Promise<void>(resolve=>server.close(()=>resolve()));await cube.core.shutdown();await source.release();await external.release();assert.equal(source.initPromise,null);assert.equal(external.initPromise,null);}
 const after={source:hash(sourceDb),inputs:files.map(hash),size:fs.statSync(sourceDb).size,mtime_ns:fs.statSync(sourceDb,{bigint:true}).mtimeNs.toString()};assert.deepEqual(after,before);
 fs.writeFileSync(path.join(work,'evidence.json'),JSON.stringify({kind:'SYNTHETIC_CROSS_DRIVER_MECHANISM_NOT_SNOWFLAKE',readonly_source:sourceDb,before,after,nativeWriteError,driver_connections_released:source.initPromise===null&&external.initPromise===null,maxRSS_KiB:process.resourceUsage().maxRSS,sourceQueries,externalQueries,events,responses},null,2));
});
