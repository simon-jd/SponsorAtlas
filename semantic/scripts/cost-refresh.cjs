// Private Task10 process. No gateway, public route, production config or source guard changes.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto');
const yaml=require('js-yaml'),express=require('express'),jwt=require('jsonwebtoken');
const {CubejsServer}=require('@cubejs-backend/server');
const {DuckDBInstance}=require('@duckdb/node-api');
const {LifecycleSnowflakeDriver}=require('../snowflake-lifecycle.cjs');
const {modelRepository,projections,trialWindow,rejectPasswords,assertReadQuery,secondaryRolesDisabled}=require('../source.cjs');
const {ExternalDuckDBDriver}=require('../external.cjs');
const {acquireWorkLock}=require('../maintenance.cjs');
const ROOT=path.resolve(__dirname,'../..');
const queries=[
 {measures:['lca.case_count','lca.certified_rate'],dimensions:['lca.fiscal_year']},
 {measures:['lca.wage_p50','lca.wage_p75','lca.wage_p90']},
 {measures:['postings.count']},
 {measures:['wages.oews_median'],filters:[{member:'wages.soc',operator:'equals',values:['15-1252']},{member:'wages.area',operator:'equals',values:['1:99']},{member:'wages.survey_year',operator:'equals',values:['2025']}]},
];
function settings(env=process.env,now=Date.now()){
 rejectPasswords(env);
 if(env.SPONSORATLAS_COST_MODE!=='production'||env.SPONSORATLAS_COST_OPT_IN!=='YES'||env.SPONSORATLAS_COST_EXPERIMENT_OPT_IN!=='YES'||env.SPONSORATLAS_WAREHOUSE_EXCLUSIVE!=='YES')throw Error('Explicit private production experiment required');
 const balance=Number(env.SPONSORATLAS_TRIAL_REMAINING_CREDITS),allowance=Number(env.SPONSORATLAS_COST_MAX_CREDITS);
 if(!Number.isFinite(balance)||!Number.isFinite(allowance)||allowance<=0||allowance>balance)throw Error('Finite trial credit allowance required');
 const window=trialWindow(env,now),seconds=Number(env.SPONSORATLAS_COST_SECONDS);
 if(!Number.isInteger(seconds)||seconds<1||seconds>60||window.cutoff-now<seconds*1000+60000)throw Error('Finite private refresh budget required');
 if(!/^ANALYTICS\.TASK10_[A-Z0-9_]{1,40}$/.test(env.SPONSORATLAS_COST_SCHEMA||''))throw Error('Dedicated allowlisted namespace required');
 if(!/^[A-Z0-9_]{1,40}$/.test(env.SPONSORATLAS_COST_RUN_ID||'')||!['baseline','clustered','small','cache_prime','cache_repeat'].includes(env.SPONSORATLAS_COST_PHASE))throw Error('Private experiment phase required');
 if(!['TRUE','FALSE'].includes(env.SPONSORATLAS_COST_CACHE))throw Error('Explicit result cache setting required');
 if(env.SNOWFLAKE_COST_USER!=='COST_EXPERIMENT_SVC'||env.SNOWFLAKE_COST_ROLE!=='COST_EXPERIMENTER')throw Error('Dedicated private identity required');
 if(!/^[a-zA-Z0-9_.-]+$/.test(env.SNOWFLAKE_ACCOUNT||''))throw Error('Account required');
 const keyFile=path.resolve(env.SNOWFLAKE_COST_PRIVATE_KEY_FILE||'');
 if((fs.statSync(keyFile).mode&0o077)!==0)throw Error('Owner-only key required');
 const key=crypto.createPrivateKey({key:fs.readFileSync(keyFile),passphrase:env.SNOWFLAKE_COST_PRIVATE_KEY_PASSPHRASE});
 if(key.asymmetricKeyType!=='rsa'||key.asymmetricKeyDetails.modulusLength<2048)throw Error('RSA 2048+ required');
 const work=path.resolve(env.SPONSORATLAS_COST_CUBE_WORK||'');
 if(!work.startsWith(path.join(ROOT,'.tools/task10/cube')+path.sep)||fs.existsSync(work))throw Error('Fresh private owned work required');
 return {schema:env.SPONSORATLAS_COST_SCHEMA,work,cutoff:Math.min(window.cutoff,now+seconds*1000),cached:env.SPONSORATLAS_COST_CACHE==='TRUE',
  tag:`SponsorAtlas_Task10_${env.SPONSORATLAS_COST_RUN_ID}_${env.SPONSORATLAS_COST_PHASE}_cube`,
  config:{account:env.SNOWFLAKE_ACCOUNT,username:'COST_EXPERIMENT_SVC',role:'COST_EXPERIMENTER',warehouse:'SPONSORATLAS_XS',database:'ANALYTICS',authenticator:'SNOWFLAKE_JWT',privateKeyPath:keyFile,privateKeyPass:env.SNOWFLAKE_COST_PRIVATE_KEY_PASSPHRASE,executionTimeout:30,clientSessionKeepAlive:false,readOnly:true,identIgnoreCase:false}};
}
class PrivateSource extends LifecycleSnowflakeDriver{
 constructor(config){super({...config.config,queryTag:config.tag},config.cutoff);this.settings=config;this.queryIds=[];this.statementCount=0;}
 async initConnection(){const c=await super.initConnection();await this.execute(c,`ALTER SESSION SET USE_CACHED_RESULT=${this.settings.cached?'TRUE':'FALSE'}, STATEMENT_QUEUED_TIMEOUT_IN_SECONDS=10`,[],false);return c;}
 check(sql){this.guard();if(/\b(?:FROM|JOIN)\s+ANALYTICS\.MARTS\./i.test(sql))throw Error('Private benchmark must use its isolated namespace');assertReadQuery(sql.replaceAll(this.settings.schema,'ANALYTICS.MARTS'));}
 query(sql,values=[]){try{this.check(sql);return super.query(sql,values);}catch(e){return Promise.reject(e);}}
 downloadQueryResults(sql,values,options){try{this.check(sql);return super.downloadQueryResults(sql,values,options);}catch(e){return Promise.reject(e);}}
 memory(sql,values){try{this.check(sql);return super.memory(sql,values);}catch(e){return Promise.reject(e);}}
 stream(sql,values,options){try{this.check(sql);return super.stream(sql,values,options);}catch(e){return Promise.reject(e);}}
 executeCancelable(connection,options,onComplete){
  if(++this.statementCount>60)throw Error('Private query count budget exceeded');
  return super.executeCancelable(connection,options,(statement,rows)=>{this.guard();const id=statement.getQueryId();if(id)this.queryIds.push(id);return onComplete(statement,rows);});
 }
 async testConnection(){const [r]=await this.query('SELECT CURRENT_USER() AS "u",CURRENT_ROLE() AS "r",CURRENT_WAREHOUSE() AS "w",CURRENT_SECONDARY_ROLES() AS "s"',[]);if(r.u!=='COST_EXPERIMENT_SVC'||r.r!=='COST_EXPERIMENTER'||r.w!=='SPONSORATLAS_XS'||!secondaryRolesDisabled(r.s))throw Error('Unexpected private session');
  const c=await this.getConnection(),warehouses=await this.execute(c,"SHOW WAREHOUSES LIKE 'SPONSORATLAS_XS'",[],false),w=warehouses[0];const expected=this.settings.tag.includes('_small_cube')?'Small':'X-Small';
  if(warehouses.length!==1||w.name!=='SPONSORATLAS_XS'||w.size!==expected||Number(w.auto_suspend)!==60||Number(w.min_cluster_count)!==1||Number(w.max_cluster_count)!==1||!w.resource_monitor)throw Error('Private warehouse settings differ from phase');this.warehouseState={name:w.name,size:w.size,auto_suspend:60};
 }
}
function repository(revision,schema){
 const repo=modelRepository('external-fixture',{revision});
 if(schema){const original=repo.dataSchemaFiles.bind(repo);repo.dataSchemaFiles=async dependencies=>(await original(dependencies)).map(file=>{
  if(!/cubes[/\\].*\.yml$/.test(file.fileName))return file;
  const data=yaml.load(file.content);for(const cube of data.cubes){cube.sql=projections[cube.name].replaceAll('{s}',schema);delete cube.sql_table;}
  return {...file,content:yaml.dump(data,{lineWidth:-1,noRefs:true})};
 });}
 return repo;
}
async function physical(work){
 const instance=await DuckDBInstance.create(path.join(work,'external-scratch/preaggregations.duckdb'),{access_mode:'READ_ONLY'});let c;
 try{c=await instance.connect();await c.run("SET threads=1; SET memory_limit='128MB'; SET enable_external_access=false;");
  const names=(await c.runAndReadAll("SELECT table_name FROM information_schema.tables WHERE table_schema='semantic_preagg' AND table_type='BASE TABLE' ORDER BY table_name")).getRowObjectsJson();
  if(names.length!==2)throw Error('Exactly two fresh physical rollups required');const tables=[];
  for(const {table_name:name} of names){if(!/^(lca|postings)_counts_[a-z0-9_]+$/.test(name))throw Error('Unexpected physical rollup');tables.push({name,rows:(await c.runAndReadAll('SELECT * FROM semantic_preagg.'+name)).getRowObjectsJson()});}return tables;
 }finally{if(c)c.closeSync();instance.closeSync();}
}
async function runRefresh({work,source,schema=null,cutoff=Date.now()+60000,kind='SYNTHETIC_CUBE_REFRESH_NOT_SNOWFLAKE'}){
 if(fs.existsSync(work))throw Error('Fresh work required');
 process.env.CUBEJS_DEV_MODE='false';process.env.CUBEJS_TELEMETRY='false';process.env.CUBEJS_DB_TYPE=schema?'snowflake':'duckdb';process.env.CUBEJS_CACHE_AND_QUEUE_DRIVER='memory';process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';
 const lease=acquireWorkLock(work,'refresh'),revision=crypto.randomBytes(16).toString('hex'),secret=crypto.randomUUID();
 const external=new ExternalDuckDBDriver(work);const cube=new CubejsServer({devServer:false,apiSecret:secret,scheduledRefreshTimer:false,preAggregationsSchema:'semantic_preagg',repositoryFactory:()=>repository(revision,schema),driverFactory:()=>source,externalDbType:'duckdb',externalDriverFactory:()=>external,orchestratorOptions:{queryCacheOptions:{queueOptions:{concurrency:1}},preAggregationsOptions:{externalRefresh:false,queueOptions:{concurrency:1}}},logger:()=>{}});
 let server,complete=false,evidence;const started=Date.now();
 const timer=setTimeout(()=>{void source.release();},Math.max(1,cutoff-Date.now()));
 try{
  await source.testConnection();await external.query('CREATE SCHEMA IF NOT EXISTS semantic_preagg',[]);const app=express();app.use(express.json({limit:'16kb'}));app.use((req,res,next)=>req.method==='POST'&&req.path==='/cubejs-api/v1/load'?next():res.sendStatus(404));
  await cube.core.initApp(app);server=http.createServer(app);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const token=jwt.sign({sub:'private-cost-refresh'},secret,{expiresIn:'2m'}),results=[];
  for(const query of queries){let result;
   while(Date.now()<cutoff){const response=await fetch(`http://127.0.0.1:${server.address().port}/cubejs-api/v1/load`,{method:'POST',headers:{Authorization:token,'Content-Type':'application/json'},body:JSON.stringify({query,cache:'must-revalidate'}),signal:AbortSignal.timeout(Math.max(1,Math.min(30000,cutoff-Date.now())))});result=await response.json();if(result.error==='Continue wait')continue;if(!response.ok||result.error)throw Error('Private Cube REST query failed'+(schema?'':': '+JSON.stringify(result)));break;}
   if(Date.now()>=cutoff||!result?.data)throw Error('Private Cube absolute deadline');results.push({data:result.data,usedPreAggregations:result.usedPreAggregations});
  }
  for(const i of [0,2])if(!Object.keys(results[i].usedPreAggregations||{}).some(k=>k.includes('counts')))throw Error('Physical rollup not used');
  evidence={kind,revision,elapsed_seconds:(Date.now()-started)/1000,results,query_ids:source.queryIds||[],query_tag:source.settings?.tag,warehouse:source.warehouseState,use_cached_result:source.settings?.cached,persisted_result_hit:'UNKNOWN',result_sha256:crypto.createHash('sha256').update(JSON.stringify(results.map(r=>r.data))).digest('hex')};complete=true;
 }finally{
  clearTimeout(timer);if(server)await new Promise(resolve=>server.close(resolve));await cube.core.shutdown();await source.release();await external.release();
  try{if(complete){const tables=await physical(work);
   for(const [prefix,key,expected] of [['lca_counts_','lca__case_count',evidence.results[0].data.reduce((s,r)=>s+Number(r['lca.case_count']),0)],['postings_counts_','postings__count',Number(evidence.results[2].data[0]['postings.count'])]]){const table=tables.find(t=>t.name.startsWith(prefix));if(!table||table.rows.reduce((s,r)=>s+Number(r[key]),0)!==expected)throw Error('Closed physical rollup disagrees with REST');}
   evidence.closed_file_physical_tables=tables;evidence.rest_elapsed_seconds=evidence.elapsed_seconds;evidence.elapsed_seconds=(Date.now()-started)/1000;evidence.maxRSS_KiB=process.resourceUsage().maxRSS;lease.commit(revision);fs.writeFileSync(path.join(work,'cost-refresh.json'),JSON.stringify(evidence,null,2));
  }}finally{lease.release();}
 }
 return evidence;
}
module.exports={settings,PrivateSource,repository,runRefresh,queries};
if(require.main===module){let config;try{config=settings();}catch(error){console.error('Private cost admission failed ('+error.name+')');process.exitCode=1;}
 if(config)runRefresh({...config,source:new PrivateSource(config),kind:'ACTUAL_SNOWFLAKE_CUBE_REFRESH'}).then(()=>process.exit(0)).catch(error=>{console.error('Private cost refresh failed ('+error.name+')');process.exit(1);});
}
