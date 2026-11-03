// Private, exclusive maintenance worker. It adds no public HTTP/admin route.
process.env.CUBEJS_DEV_MODE='false';process.env.CUBEJS_TELEMETRY='false';
process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';process.env.CUBEJS_CACHE_AND_QUEUE_DRIVER='memory';
const fs=require('node:fs');const path=require('node:path');const http=require('node:http');const crypto=require('node:crypto');
const {sourceSettings,modelRepository}=require('../source.cjs');
const {acquireWorkLock}=require('../maintenance.cjs');
const {DuckDBInstance}=require('@duckdb/node-api');
const settings=sourceSettings();process.env.CUBEJS_DB_TYPE=settings.source==='snowflake'?'snowflake':'duckdb';
const jwt=require('jsonwebtoken');const express=require('express');const {CubejsServer}=require('@cubejs-backend/server');
async function physicalTables(work){
 const database=path.join(work,settings.source==='parquet'?'scratch/cube.duckdb':'external-scratch/preaggregations.duckdb');
 if(!fs.existsSync(database))return [];
 const instance=await DuckDBInstance.create(database,{access_mode:'READ_ONLY'});let connection;
 try{
  connection=await instance.connect();await connection.run("SET threads=1; SET memory_limit='128MB'; SET enable_external_access=false;");
  const reader=await connection.runAndReadAll("SELECT table_name FROM information_schema.tables WHERE table_schema='semantic_preagg' AND table_type='BASE TABLE' ORDER BY table_name");
  const names=reader.getRowObjectsJson().map(r=>r.table_name);if(names.length>256)throw Error('Maintenance table inventory cap exceeded');
  const tables=[];
  for(const name of names){
   if(!/^(lca|postings)_counts_[a-z0-9_]+$/.test(name))throw Error('Unexpected physical rollup');
   const rows=await connection.runAndReadAll('SELECT * FROM semantic_preagg.'+name);tables.push({table_name:name,rows:rows.getRowObjectsJson()});
  }
  return tables;
 }finally{if(connection)connection.closeSync();instance.closeSync();}
}
(async()=>{
 const work=path.resolve(process.env.SEMANTIC_WORK||'');
 if(!process.env.SEMANTIC_WORK||!process.env.CUBEJS_API_SECRET)throw Error('Explicit private Cube work directory and API secret required');
 const lease=acquireWorkLock(work,'refresh'),revision=crypto.randomBytes(16).toString('hex');
 let cube,server,driver,evidence;const started=Date.now();
 try{
  const before=await physicalTables(work);
  try{
   const config={...require('../cube.cjs'),repositoryFactory:()=>modelRepository(settings.source,{work,revision}),logger:()=>{}};
   // Preflight and query execution must share the same native instance. Two independent
   // writable DuckDB catalogs for one path can overwrite each other when closed.
   const factory=config.driverFactory;config.driverFactory=context=>driver||(driver=factory(context));
   cube=new CubejsServer(config);
   const app=express();app.use(express.json({limit:'16kb'}));
   app.use((req,res,next)=>req.method==='POST'&&req.path==='/cubejs-api/v1/load'?next():res.sendStatus(404));
   await cube.core.initApp(app);driver=await cube.getDriver({dataSource:'default'});await driver.testConnection();
   if(settings.source==='snowflake')await driver.preflight();
   server=http.createServer(app);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
   const token=jwt.sign({sub:'sponsoratlas-controlled-refresh'},process.env.CUBEJS_API_SECRET,{expiresIn:'5m'});
   const results=[];
   for(const query of [{measures:['lca.case_count','lca.certified_rate'],dimensions:['lca.fiscal_year']},{measures:['postings.count']}]){
    let complete=false;
    while(Date.now()-started<240000){
     const response=await fetch(`http://127.0.0.1:${server.address().port}/cubejs-api/v1/load`,{
      method:'POST',headers:{Authorization:token,'Content-Type':'application/json'},body:JSON.stringify({query,cache:'must-revalidate'}),signal:AbortSignal.timeout(30000)});
     const body=await response.json();if(body.error==='Continue wait')continue;
     if(!response.ok||body.error)throw Error('Cube refresh query failed');
     if(!Object.keys(body.usedPreAggregations||{}).some(k=>k.includes('counts')))throw Error('Physical counts rollup was not used');
     results.push({query,data:body.data,usedPreAggregations:body.usedPreAggregations});complete=true;break;
    }
    if(!complete)throw Error('Controlled refresh deadline exceeded');
   }
   evidence={kind:'CONTROLLED_CUBE_PHYSICAL_REFRESH',source:settings.source,revision,results};
  }finally{
   if(server)await new Promise(resolve=>server.close(resolve));
   if(cube)await cube.core.shutdown();if(driver)await driver.release();
  }
  // Reopen the closed file independently before promoting any success state.
  const tables=(await physicalTables(work)).filter(t=>!before.some(old=>old.table_name===t.table_name));
  if(tables.length!==2)throw Error('Both newly built physical rollups must persist after all drivers close');
  for(const [prefix,key,expected] of [['lca_counts_','lca__case_count',evidence.results[0].data.reduce((s,r)=>s+Number(r['lca.case_count']),0)],
                                   ['postings_counts_','postings__count',Number(evidence.results[1].data[0]['postings.count'])]]){
   const table=tables.find(t=>t.table_name.startsWith(prefix));
   if(!table||table.rows.reduce((s,r)=>s+Number(r[key]),0)!==expected)throw Error('Persisted physical rollup disagrees with REST result');
  }
  evidence.closed_file_physical_tables=tables;evidence.seconds=(Date.now()-started)/1000;evidence.maxRSS_KiB=process.resourceUsage().maxRSS;
  lease.commit(revision);fs.writeFileSync(path.join(work,'refresh-evidence.json'),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify(evidence));
 }finally{lease.release();}
})().then(()=>process.exit(0)).catch(error=>{console.error('Controlled Cube refresh failed ('+error.name+'); details suppressed');process.exit(1);});
