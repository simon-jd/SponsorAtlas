// Real Cube REST gateway with explicit source selection and bounded loopback transport.
process.env.CUBEJS_DEV_MODE='false';process.env.CUBEJS_TELEMETRY='false';
const {sourceSettings}=require('./source.cjs');
const {bindSettings}=require('./bind.cjs');
const bind=bindSettings();
let settings;
try{settings=sourceSettings();}catch(e){console.error(e.message);process.exit(1);}
process.env.CUBEJS_DB_TYPE=settings.source==='snowflake'?'snowflake':'duckdb';
process.env.CUBEJS_TELEMETRY='false';
process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';
process.env.CUBEJS_CACHE_AND_QUEUE_DRIVER='memory';
process.env.CUBEJS_DEV_MODE='false';
process.env.CUBEJS_WEB_SOCKETS='false';
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const express=require('express');
const {CubejsServer}=require('@cubejs-backend/server');
const {acquireWorkLock}=require('./maintenance.cjs');
let workLease;try{workLease=acquireWorkLock(process.env.SEMANTIC_WORK,'gateway');}catch(error){console.error(error.message);process.exit(1);}
process.once('exit',()=>workLease.release());
const config=require('./cube.cjs');
const sourceFactory=config.driverFactory;let sharedSource;config.driverFactory=context=>sharedSource||(sharedSource=sourceFactory(context));
(async()=>{
 const work=process.env.SEMANTIC_WORK;
 const source=settings.source==='parquet'?require('./snapshot.cjs').snapshotDirectory():work;
 const cube=new CubejsServer(config);
 const app=express();
 app.use((req,res,next)=>{
  if(!((req.path==='/cubejs-api/v1/meta'&&req.method==='GET')||(req.path==='/cubejs-api/v1/load'&&req.method==='POST')||(req.path==='/cubejs-api/v1/h3'&&req.method==='POST')))return res.status(404).json({error:'REST meta/load only'});
  if(settings.source==='snowflake'&&Date.now()>=settings.cutoff)return res.status(503).json({error:'Snowflake trial cutoff reached; select a verified Parquet snapshot explicitly'});
  next();
 });
 app.use(express.json({limit:'128kb'}));await cube.core.initApp(app);
 const driver=await cube.getDriver({dataSource:'default'});await driver.testConnection();
 let probe;
 if(settings.source==='snowflake')probe=await driver.preflight();
 else{
  probe={engine:await driver.query('select version() as engine_version',[]),settings:await driver.query("select current_setting('memory_limit') memory_limit,current_setting('threads') threads,current_setting('max_temp_directory_size') temp_limit,current_setting('enable_external_access') external_access",[])};
  for(const [name,sql] of [['external_read',"select * from read_csv('/etc/passwd')"],['source_overwrite',`copy (select 1) to '${path.join(source,'lca.parquet').replaceAll("'","''")}' (format parquet)`]]){
   try{await driver.query(sql,[]);throw Error(name+' unexpectedly succeeded');}catch(e){if(e.message.includes('unexpectedly'))throw e;probe[name]=e.message;}
  }
  try{const fd=fs.openSync(path.join(source,'lca.parquet'),'r+');fs.closeSync(fd);throw Error('source OS write access unexpectedly succeeded');}catch(e){if(e.message.includes('unexpectedly'))throw e;probe.source_os_write=e.message;}
 }
 app.post('/cubejs-api/v1/h3',require('./h3-api.cjs').handler(settings,driver));
 fs.writeFileSync(path.join(work,'driver-evidence.json'),JSON.stringify(probe,null,2));
 const server=http.createServer(app);await new Promise(resolve=>server.listen(bind.port,bind.host,resolve));
 console.log('Cube 1.8.0 '+settings.source+' REST listening on '+bind.host+':'+server.address().port);
 const record=()=>fs.writeFileSync(path.join(work,'resource.json'),JSON.stringify({pid:process.pid,...process.memoryUsage(),maxRSS_KiB:process.resourceUsage().maxRSS,bind:server.address()}));
 const resource=setInterval(record,250);resource.unref();
 let stopping=false;
 const stop=async()=>{if(stopping)return;stopping=true;clearInterval(resource);clearInterval(expiry);record();await new Promise(resolve=>server.close(resolve));await cube.core.shutdown();await driver.release();process.exit(0);};
 const expiry=setInterval(()=>{if(settings.source==='snowflake'&&Date.now()>=settings.cutoff)void stop();},1000);expiry.unref();
 process.once('SIGTERM',stop);
})().catch(e=>{console.error(settings.source==='snowflake'?'Snowflake startup/query preflight failed; details suppressed':e);process.exit(1);});
