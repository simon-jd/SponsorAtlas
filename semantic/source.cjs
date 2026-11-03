const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const yaml = require('js-yaml');
const {FileRepository} = require('@cubejs-backend/server-core');
const {LifecycleSnowflakeDriver} = require('./snowflake-lifecycle.cjs');
const projections = require('./projections.json');
const h3=require('./h3.cjs');
const {readReadyState,validateRevision}=require('./maintenance.cjs');
const TABLES = new Set(['FACT_LCA_CASE','FACT_JOB_POSTING_DAILY','FACT_WAGE_BENCHMARK','DIM_OCCUPATION','DIM_GEOGRAPHY','DIM_DATE']);
const SAFETY_MS=420000;
function rejectPasswords(env=process.env){
 for(const [k,v] of Object.entries(env))if(v&&(/^(SNOWFLAKE_(PASSWORD|PWD|PASS)|CUBEJS_(.*_)?DB_PASS)$/.test(k)))throw Error('Password authentication is forbidden');
}
function trialWindow(env,now=Date.now()){
 const parse=name=>{const value=env[name];if(!value||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value))throw Error(name+' must be an explicit UTC timestamp');const number=Date.parse(value);if(!Number.isFinite(number)||new Date(number).toISOString().replace('.000','')!==value)throw Error('Invalid trial timestamp');return number;};
 const start=parse('SNOWFLAKE_TRIAL_STARTED_AT'),expires=parse('SNOWFLAKE_TRIAL_EXPIRES_AT');
 if(expires<=start||expires-start>30*86400000||now<start||now>=expires-SAFETY_MS)throw Error('Trial inactive or within seven-minute shutdown margin');
 return {start,expires,cutoff:expires-SAFETY_MS};
}
function sourceSettings(env=process.env,now=Date.now()){
 rejectPasswords(env);
 if(!['parquet','snowflake'].includes(env.SEMANTIC_SOURCE))throw Error('SEMANTIC_SOURCE must explicitly be parquet or snowflake');
 if(env.SEMANTIC_SOURCE==='parquet')return {source:'parquet'};
 if(env.SEMANTIC_SNOWFLAKE_OPT_IN!=='1')throw Error('Explicit SEMANTIC_SNOWFLAKE_OPT_IN=1 required');
 const window=trialWindow(env,now);
 for(const [name,value] of Object.entries({SNOWFLAKE_USER:'CUBE_SVC',SNOWFLAKE_ROLE:'ANALYST',SNOWFLAKE_WAREHOUSE:'SPONSORATLAS_XS',SNOWFLAKE_DATABASE:'ANALYTICS',SNOWFLAKE_SCHEMA:'MARTS'}))if(env[name]&&env[name]!==value)throw Error('Invalid '+name);
 if(!env.SNOWFLAKE_ACCOUNT||!env.SNOWFLAKE_PRIVATE_KEY_FILE)throw Error('Snowflake account and RSA private key file required');
 if(!/^[a-zA-Z0-9_.-]+$/.test(env.SNOWFLAKE_ACCOUNT))throw Error('Invalid Snowflake account');
 const keyFile=path.resolve(env.SNOWFLAKE_PRIVATE_KEY_FILE);
 if((fs.statSync(keyFile).mode&0o077)!==0)throw Error('Private key must be owner-only');
 let key;
 try{key=crypto.createPrivateKey({key:fs.readFileSync(keyFile),passphrase:env.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE});}catch{throw Error('Invalid RSA private key or key passphrase');}
 if(key.asymmetricKeyType!=='rsa'||key.asymmetricKeyDetails.modulusLength<2048)throw Error('RSA key must be at least 2048 bits');
 return {source:'snowflake',...window,config:{account:env.SNOWFLAKE_ACCOUNT,username:'CUBE_SVC',role:'ANALYST',warehouse:'SPONSORATLAS_XS',database:'ANALYTICS',authenticator:'SNOWFLAKE_JWT',privateKeyPath:keyFile,privateKeyPass:env.SNOWFLAKE_PRIVATE_KEY_PASSPHRASE,readOnly:true,executionTimeout:240,clientSessionKeepAlive:false,queryTag:'SponsorAtlas_Task7_ReadOnly'}};
}
// CURRENT_SECONDARY_ROLES() without secondary roles: {"roles":"","value":""} (observed 2026-10-31); "NONE" also accepted.
function secondaryRolesDisabled(text){
 let state;try{state=JSON.parse(text);}catch{return false;}
 return Boolean(state)&&typeof state==='object'&&!Array.isArray(state)&&['','NONE'].includes(state.value)&&(state.roles===''||(Array.isArray(state.roles)&&state.roles.length===0));
}
function assertReadQuery(sql){
 if(typeof sql!=='string'||!/^\s*SELECT\b/i.test(sql)||/;|--|\/\*/.test(sql))throw Error('Only one SELECT statement is permitted');
 if(/\b(RAW|STAGING|INTERMEDIATE|FACT_MY_APPLICATIONS)\b/i.test(sql)||/\bSYSTEM\$/i.test(sql))throw Error('Only public marts are permitted');
 // No callable external source/table functions or SQL side effects in the named-model interface.
 if(/\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|MERGE|COPY|CALL|PUT|GET|SYSTEM\$|RESULT_SCAN|IDENTIFIER|TO_QUERY)\b/i.test(sql))throw Error('Non-read semantic SQL rejected');
 const tableSql=sql.replace(/\bEXTRACT\s*\(\s*[A-Z_]+\s+FROM\s+/gi,'EXTRACT(');
 for(const match of tableSql.matchAll(/\b(?:FROM|JOIN)\s+([A-Z_][A-Z_0-9.]*)/gi)){
  if(!/^ANALYTICS\.MARTS\./i.test(match[1])||!TABLES.has(match[1].split('.').at(-1).toUpperCase()))throw Error('Source table is outside the public marts allowlist');
 }
}
class ReadOnlySnowflakeDriver extends LifecycleSnowflakeDriver{
 constructor(settings,clock=Date.now){super({...settings.config,password:undefined,privateKey:undefined,host:undefined,region:undefined,oauthToken:undefined,oauthTokenPath:undefined,identIgnoreCase:false},settings.cutoff,clock);this.settings=settings;}
 checked(sql,run){try{this.guard();assertReadQuery(sql);return run();}catch(e){return Promise.reject(e);}}
 query(sql,values=[]){return this.checked(sql,()=>super.query(sql,values));}
 downloadQueryResults(sql,values,options){return this.checked(sql,()=>super.downloadQueryResults(sql,values,options));}
 memory(sql,values){return this.checked(sql,()=>super.memory(sql,values));}
 stream(sql,values,options){return this.checked(sql,()=>super.stream(sql,values,options));}

 async testConnection(){await this.query('SELECT 1',[]);}
 async preflight(){
  this.guard();
  const identity=await this.query('SELECT CURRENT_USER() AS "user",CURRENT_ROLE() AS "role",CURRENT_WAREHOUSE() AS "warehouse",CURRENT_SECONDARY_ROLES() AS "secondary_roles"',[]);
  const r=identity[0];if(!secondaryRolesDisabled(r.secondary_roles))throw Error('Secondary roles must be disabled');if(r.user!=='CUBE_SVC'||r.role!=='ANALYST'||r.warehouse!=='SPONSORATLAS_XS')throw Error('Unexpected Snowflake session identity');
  // SHOW is narrowly scoped metadata and does not alter warehouse configuration.
  this.guard();const c=await this.getConnection();const warehouses=await this.execute(c,"SHOW WAREHOUSES LIKE 'SPONSORATLAS_XS'",[],false);
  const w=warehouses[0];if(warehouses.length!==1||w.name!=='SPONSORATLAS_XS'||w.size!=='X-Small'||Number(w.auto_suspend)!==60||Number(w.min_cluster_count)!==1||Number(w.max_cluster_count)!==1)throw Error('Warehouse must be X-Small with auto_suspend=60');
  const checks=[];
  for(const [table,key] of [['dim_occupation','occupation_key'],['dim_geography','geography_key'],['dim_date','date_key'],['fact_lca_case','case_number'],['fact_job_posting_daily',"posting_id||':'||CAST(observation_date AS VARCHAR)"],['fact_wage_benchmark',"soc_code||':'||area_type||':'||area_code||':'||CAST(survey_year AS VARCHAR)"]]){
   const rows=await this.query(`SELECT COUNT(*) AS "rows",COUNT(DISTINCT ${key}) AS "unique_rows" FROM ANALYTICS.MARTS.${table}`,[]);
   if(String(rows[0].rows)!==String(rows[0].unique_rows))throw Error('Duplicate or null marts key');checks.push({table,rows:rows[0].rows});
  }
  for(const [name,sql] of Object.entries(projections)){
   const [p]=await this.query(`SELECT COUNT(*) AS "rows" FROM (${sql.replaceAll('{s}','ANALYTICS.MARTS')}) projected`,[]);
   const table={lca:'fact_lca_case',postings:'fact_job_posting_daily',wages:'fact_wage_benchmark'}[name];
   if(String(p.rows)!==String(checks.find(c=>c.table===table).rows))throw Error('Marts projection fanout/drop');
  }
  return {identity,warehouse:{name:w.name,size:w.size,auto_suspend:w.auto_suspend},checks,kind:'ACTUAL_SNOWFLAKE_PREFLIGHT'};
 }
}
class SourceRepository extends FileRepository{
 constructor(source,options={}){super('model');this.source=source;this.revision=options.revision===undefined?(readReadyState(options.work||process.env.SEMANTIC_WORK)?.revision||null):validateRevision(options.revision);}
 localPath(){return path.join(__dirname,'model');}
 async dataSchemaFiles(includeDependencies=false){
  const files=(await super.dataSchemaFiles(includeDependencies)).filter(file=>process.env.SEMANTIC_H3_ENABLED==='1'||!/(?:^|[/\\])h3\.yml$/.test(file.fileName));if(this.source==='parquet'&&!this.revision)return files;
  return files.map(file=>{
   if(!/cubes[/\\].*\.yml$/.test(file.fileName))return file;
   const data=yaml.load(file.content);
   for(const cube of data.cubes){if(this.source==='snowflake'){cube.sql=cube.name==='h3'?h3.nativeSql():projections[cube.name].replaceAll('{s}','ANALYTICS.MARTS');delete cube.sql_table;}for(const rollup of cube.pre_aggregations||[]){if(this.source!=='parquet')rollup.external=true;if(this.revision)rollup.refresh_key={sql:`SELECT '${this.revision}'`};}}
   return {...file,content:yaml.dump(data,{lineWidth:-1,noRefs:true})};
  });
 }
}
module.exports={sourceSettings,rejectPasswords,trialWindow,ReadOnlySnowflakeDriver,modelRepository:(source,options)=>new SourceRepository(source,options),assertReadQuery,secondaryRolesDisabled,projections};
