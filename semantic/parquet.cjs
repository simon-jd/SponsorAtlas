const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {modelRepository}=require('./source.cjs');
const h3=require('./h3.cjs');
const source=require('./snapshot.cjs').snapshotDirectory();
const { DuckDBDriver } = require('@cubejs-backend/duckdb-driver');
const work = path.resolve(process.env.SEMANTIC_WORK || '');
if (!process.env.SEMANTIC_WORK || !process.env.CUBEJS_API_SECRET) throw Error('SEMANTIC_WORK and server-side CUBEJS_API_SECRET required');
const scratch=path.join(work,process.env.SEMANTIC_H3_ENABLED==='1'?'scratch-h3':'scratch');fs.mkdirSync(scratch,{recursive:true});
const sources=['lca','postings','wages'].map(name=>({name,file:path.join(source,name+'.parquet')}));
for(const {file} of sources) {
 if(!fs.statSync(file).isFile() || (fs.statSync(file).mode & 0o222)) throw Error('Parquet must have all write bits removed: '+file);
}
const manifest=JSON.parse(fs.readFileSync(path.join(source,'export-evidence.json'),'utf8'));
if(!['ACTUAL_PUBLIC_MARTS','SYNTHETIC_ANALYTICS_TEST_ONLY','ACTUAL_SNOWFLAKE_PUBLIC_MARTS'].includes(manifest.kind))throw Error('Unknown snapshot provenance');
if(manifest.kind==='ACTUAL_SNOWFLAKE_PUBLIC_MARTS'&&(!manifest.validation?.all_seven_match||manifest.validation.engine!=='v1.5.5'))throw Error('Snowflake snapshot must pass native validation before serving');
let revision=sources.map(({name,file})=>{
 const expected=manifest.exports.find(x=>x.path===file);
 const actual=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
 if(!expected||expected.sha256!==actual||expected.size!==fs.statSync(file).size)throw Error('Export manifest mismatch: '+file);
 if(manifest.kind==='ACTUAL_SNOWFLAKE_PUBLIC_MARTS'&&manifest.validation.checksums[name]!==actual)throw Error('Validated snapshot fingerprint mismatch');
 return actual;
}).join(':');
const h3Snapshot=h3.optionalSnapshot();
if(h3Snapshot){sources.push({name:'h3',file:h3Snapshot.file});revision+=':'+h3Snapshot.manifest.sha256;}
const marker=path.join(scratch,'snapshot.txt');
if(fs.existsSync(marker)&&fs.readFileSync(marker,'utf8')!==revision)throw Error('Snapshot changed: stop services and run semantic/run.sh clean before serving');
fs.writeFileSync(marker,revision);
const quote = x=>"'"+x.replaceAll("'","''")+"'";
const init = `SET threads=1; SET max_temp_directory_size='256MB'; SET temp_directory=${quote(path.join(scratch,'tmp'))}; `+
 sources.map(({name,file})=>`CREATE OR REPLACE VIEW mart_${name} AS SELECT * FROM read_parquet(${quote(file)});`).join(' ')+
 `SET allowed_paths=[${sources.map(s=>quote(s.file)).join(',')}]; SET enable_external_access=false;`;
class BoundedDuckDBDriver extends DuckDBDriver {
 async testConnection() {
  await super.testConnection();
  const [r]=await this.query("select current_setting('threads') threads, current_setting('memory_limit') memory_limit, current_setting('max_temp_directory_size') temp_limit, current_setting('enable_external_access') external_access",[]);
  if(Number(r.threads)!==1 || r.external_access!==false || r.memory_limit!=='488.2 MiB' || r.temp_limit!=='244.1 MiB') throw Error('Bounded source configuration failed');
 }
}
module.exports = {
 schemaPath:'model', repositoryFactory:()=>modelRepository('parquet'), devServer:false, apiSecret:process.env.CUBEJS_API_SECRET,
 scheduledRefreshTimer:false, preAggregationsSchema:'semantic_preagg',
 contextToApiScopes:()=>['data','meta'],
 queryRewrite:query=>{
  if(h3.validateQuery(query,Boolean(h3Snapshot)))return query;
  const members=[...(query.measures||[]),...(query.dimensions||[]),...(query.timeDimensions||[]).map(x=>x.dimension)];
  const visit=f=>{if(f.member)members.push(f.member);for(const k of ['and','or'])for(const child of f[k]||[])visit(child);};
  for(const f of query.filters||[])visit(f);
  for(const m of members)if(typeof m!=='string'||!/^((lca|postings|wages)|((lca|posting|wage)_metrics))\.[a-z_0-9]+$/.test(m))throw Object.assign(new Error('Only named semantic members are allowed'),{type:'UserError'});
  if(new Set(members.map(m=>m.split('.')[0])).size>1)throw Object.assign(new Error('Query one fact grain or view at a time'),{type:'UserError'});
  return query;
 },
 driverFactory:()=>new BoundedDuckDBDriver({databasePath:path.join(scratch,'cube.duckdb'),initSql:init}),
 orchestratorOptions:{queryCacheOptions:{queueOptions:{concurrency:1}},preAggregationsOptions:{externalRefresh:false,queueOptions:{concurrency:1}}},
 logger:(message,params)=>{if(message.includes('error')||message.includes('Error'))console.error(message,params);},
};
