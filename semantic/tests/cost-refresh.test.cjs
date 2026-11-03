const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
process.env.CUBEJS_TELEMETRY='false';process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';
const {DuckDBDriver}=require('@cubejs-backend/duckdb-driver');
const {DuckDBInstance}=require('@duckdb/node-api');
const {settings,runRefresh,repository}=require('../scripts/cost-refresh.cjs');
const {assertReadQuery}=require('../source.cjs');
const ROOT=path.resolve(__dirname,'../..');
test('private settings fail closed; production source guard still rejects lab SQL',()=>{
 assert.throws(()=>settings({}),/production/);
 assert.throws(()=>assertReadQuery('SELECT * FROM ANALYTICS.TASK10_TEST.fact_lca_case'),/allowlist/);
});
test('private repository reuses canonical public projections without private fields',async()=>{
 const files=await repository('a'.repeat(32),'ANALYTICS.TASK10_TEST').dataSchemaFiles();
 const text=files.map(f=>f.content).join('\n');assert.match(text,/ANALYTICS\.TASK10_TEST/);assert.doesNotMatch(text,/fact_my_applications|payload_json|worksite_state/);
});
test('native Cube REST measures seven metrics and verifies new closed physical rollups in two fresh phases',async()=>{
 const base=fs.mkdtempSync(path.join(ROOT,'.tools/task10/cube-native-')),database=path.join(base,'source.duckdb');
 let writer=new DuckDBDriver({databasePath:database,initSql:"SET threads=1; SET memory_limit='128MB';"});
 for(const name of ['lca','postings','wages'])await writer.query(`CREATE TABLE mart_${name} AS SELECT * FROM read_parquet('${path.join(ROOT,'.tools/semantic/fixture',name+'.parquet')}')`,[]);
 await writer.release();fs.chmodSync(database,0o444);
 class ReadOnlySource extends DuckDBDriver{
  readOnly(){return true;}
  async init(){const instance=await DuckDBInstance.create(database,{access_mode:'READ_ONLY'}),defaultConnection=await instance.connect();await this.configureConnection(defaultConnection);return {instance,defaultConnection};}
  async query(sql,values=[],options){assert.match(sql,/^\s*SELECT\b/i);return super.query(sql,values,options);}
 }
 try{
  const results=[];
  for(const phase of ['before','after'])results.push(await runRefresh({work:path.join(base,phase),source:new ReadOnlySource({databasePath:database,initSql:"SET threads=1; SET memory_limit='128MB'; SET enable_external_access=false;"})}));
  assert.equal(results[0].kind,'SYNTHETIC_CUBE_REFRESH_NOT_SNOWFLAKE');
  assert.equal(results[0].results.length,4);assert.equal(results[0].result_sha256,results[1].result_sha256);
  assert.notEqual(results[0].revision,results[1].revision);
  assert.equal(results[0].closed_file_physical_tables.length,2);
  assert.ok(results[1].closed_file_physical_tables.every(t=>!results[0].closed_file_physical_tables.some(old=>old.name===t.name)));
  assert.deepEqual(results[0].query_ids,[],'local measurements never invent remote IDs');
  fs.writeFileSync(path.join(ROOT,'.tools/task10/cube-native-evidence.json'),JSON.stringify(results,null,2));
 }finally{fs.rmSync(base,{recursive:true,force:true});}
});
