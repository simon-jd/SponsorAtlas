const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');const http=require('node:http');
const {createRequire}=require('node:module');const root=path.resolve(__dirname,'../..');
const req=createRequire(path.join(root,'semantic/package.json'));
const {DuckDBDriver}=req('@cubejs-backend/duckdb-driver'),{DuckDBInstance}=req('@duckdb/node-api');
const {CubejsServer}=req('@cubejs-backend/server'),express=req('express'),jwt=req('jsonwebtoken');
const {modelRepository}=require('../../semantic/source.cjs'),{ExternalDuckDBDriver}=require('../../semantic/external.cjs');
const {acquireWorkLock,readReadyState}=require('../../semantic/maintenance.cjs');
process.env.CUBEJS_TELEMETRY='false';process.env.CUBEJS_DEV_MODE='false';process.env.CUBEJS_DB_TYPE='duckdb';
process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';process.env.CUBEJS_CACHE_AND_QUEUE_DRIVER='memory';

test('same-hour read-only source change builds new physical revision; restart reuses it; failure preserves ready state',async()=>{
 const work=fs.mkdtempSync(path.join(root,'.tools/orchestration/cube-revision-'));
 const sourceDb=path.join(work,'readonly-source.duckdb');const queryTrace=[];
 class ReadOnlySource extends DuckDBDriver{
  readOnly(){return true;}
  async init(){const instance=await DuckDBInstance.create(sourceDb,{access_mode:'READ_ONLY'});const defaultConnection=await instance.connect();await this.configureConnection(defaultConnection);return {instance,defaultConnection};}
  async query(sql,values=[],options){assert.match(sql,/^\s*SELECT\b/i);queryTrace.push(sql);return super.query(sql,values,options);}
 }
 const writer=()=>new DuckDBDriver({databasePath:sourceDb,initSql:"SET threads=1; SET memory_limit='128MB';"});
 let setup=writer();
 for(const name of ['lca','postings','wages'])await setup.query(`CREATE TABLE mart_${name} AS SELECT * FROM read_parquet('${path.join(root,'.tools/semantic/fixture',name+'.parquet')}')`,[]);
 await setup.release();fs.chmodSync(sourceDb,0o444);
 const secret=crypto.randomUUID(),token=jwt.sign({sub:'read-only-external-mechanism'},secret,{expiresIn:'5m'});
 const evidence={kind:'SYNTHETIC_READ_ONLY_EXTERNAL_REFRESH_NOT_SNOWFLAKE',runs:[]};
 async function run(candidate,{fail=false}={}){
  const lease=acquireWorkLock(work,candidate?'refresh':'gateway');
  const revision=candidate||readReadyState(work).revision;
  const source=new ReadOnlySource({databasePath:sourceDb,initSql:"SET threads=1; SET memory_limit='128MB'; SET enable_external_access=false;"});
  const writes=[];const external=new ExternalDuckDBDriver(work,sql=>writes.push(sql));
  const cube=new CubejsServer({devServer:false,apiSecret:secret,scheduledRefreshTimer:false,
   repositoryFactory:()=>modelRepository('external-fixture',candidate?{work,revision:candidate}:{work}),
   driverFactory:()=>source,externalDbType:'duckdb',externalDriverFactory:()=>external,preAggregationsSchema:'semantic_preagg',
   orchestratorOptions:{queryCacheOptions:{queueOptions:{concurrency:1}},preAggregationsOptions:{externalRefresh:false,queueOptions:{concurrency:1}}},logger:()=>{}});
  const app=express();app.use(express.json());let server;let result;let complete=false;
  try{
   assert.throws(()=>acquireWorkLock(work,'refresh'),/owned|locked/);
   await cube.core.initApp(app);server=http.createServer(app);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
   const response=await fetch(`http://127.0.0.1:${server.address().port}/cubejs-api/v1/load`,{method:'POST',headers:{Authorization:token,'Content-Type':'application/json'},body:JSON.stringify({query:{measures:['postings.count']},cache:'must-revalidate'})});
   result=await response.json();assert.equal(response.status,200,JSON.stringify(result));assert.ok(Object.keys(result.usedPreAggregations||{}).some(k=>k.includes('counts')));
   const tables=await external.query("SELECT table_name FROM information_schema.tables WHERE table_schema='semantic_preagg' AND table_type='BASE TABLE' ORDER BY table_name",[]);
   assert.ok(tables.length);if(fail)throw Error('synthetic failure after physical build before promotion');
   complete=true;evidence.runs.push({revision,data:result.data,usedPreAggregations:result.usedPreAggregations,physical_tables:tables,writes:writes.filter(s=>/^CREATE TABLE|^INSERT INTO/i.test(s))});
  }finally{
   if(server)await new Promise(resolve=>server.close(resolve));await cube.core.shutdown();await source.release();await external.release();
   if(candidate&&complete)lease.commit(candidate);lease.release();
  }
  return {result,writes};
 }
 try{
  const before=await run('1'.repeat(32));assert.equal(Number(before.result.data[0]['postings.count']),4);
  const restart=await run();assert.equal(Number(restart.result.data[0]['postings.count']),4);
  assert.deepEqual(evidence.runs[0].usedPreAggregations,evidence.runs[1].usedPreAggregations);
  assert.equal(restart.writes.filter(s=>/^CREATE TABLE|^INSERT INTO/i.test(s)).length,0,'same revision must reuse physical rollup');
  fs.chmodSync(sourceDb,0o600);setup=writer();
  await setup.query("INSERT INTO mart_postings SELECT * REPLACE ('SYNTHETIC_NEW' AS posting_id) FROM mart_postings WHERE is_active LIMIT 1",[]);assert.equal(Number((await setup.query('SELECT SUM(CASE WHEN is_active THEN 1 ELSE 0 END) AS n FROM mart_postings',[]))[0].n),5);await setup.release();fs.chmodSync(sourceDb,0o444);
  const stale=await run();assert.equal(Number(stale.result.data[0]['postings.count']),4,'same-hour prior revision proves stale rollup risk');
  const fresh=await run('2'.repeat(32));assert.equal(Number(fresh.result.data[0]['postings.count']),5);
  assert.ok(evidence.runs[3].physical_tables.some(t=>!evidence.runs[0].physical_tables.some(old=>old.table_name===t.table_name)),'new revision must persist a new physical table');
  const ready=fs.readFileSync(path.join(work,'refresh-state.json'),'utf8');
  await assert.rejects(run('3'.repeat(32),{fail:true}),/synthetic failure/);
  assert.equal(fs.readFileSync(path.join(work,'refresh-state.json'),'utf8'),ready);
  const recovered=await run();assert.equal(Number(recovered.result.data[0]['postings.count']),5);
  assert.deepEqual(evidence.runs[3].usedPreAggregations,evidence.runs.at(-1).usedPreAggregations);
  evidence.sourceQueries=queryTrace;evidence.maxRSS_KiB=process.resourceUsage().maxRSS;
  // Tracked evidence is refreshed only on request, so a normal test run leaves the tree clean.
  if(process.env.SPONSORATLAS_WRITE_EVIDENCE==='1')fs.writeFileSync(path.join(root,'orchestration/evidence/cube-external-refresh.json'),JSON.stringify(evidence,null,2));
 }finally{evidence.sourceQueries=queryTrace;fs.writeFileSync(path.join(root,'.tools/orchestration/cube-revision-debug.json'),JSON.stringify(evidence,null,2));fs.rmSync(work,{recursive:true,force:true});}
});
