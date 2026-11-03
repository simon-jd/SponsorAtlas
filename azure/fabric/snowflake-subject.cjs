// Azure-only sealed read policy. No raw SQL or driver is exposed to callers.
const fs=require('node:fs'),path=require('node:path');
const {schema,sha}=require('./content.cjs');
function templates(snapshot){
 if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(snapshot||'')||!Number.isFinite(Date.parse(snapshot))||new Date(snapshot).toISOString().replace('.000','')!==snapshot)throw Error('valid UTC snapshot required');
 const at=` AT (TIMESTAMP => '${snapshot}'::TIMESTAMP_TZ)`;
 const result={identity:'SELECT CURRENT_ACCOUNT() AS "account",CURRENT_REGION() AS "region",CURRENT_USER() AS "user",CURRENT_ROLE() AS "role"',lineage:'SELECT manifest_json AS "manifest_json" FROM ANALYTICS.DIMENSIONS_INPUT.lineage'+at};
 for(const [name,fields] of Object.entries(schema)){
  const expressions=fields.map(field=>{const [col,type]=field.split(':');const value=type==='timestamp'?`TO_CHAR(${col}, 'YYYY-MM-DD"T"HH24:MI:SS.FF9')`:type==='date'?`TO_CHAR(${col}, 'YYYY-MM-DD')`:type==='bool'?`CASE WHEN ${col} IS NULL THEN NULL WHEN ${col} THEN '1' ELSE '0' END`:`CAST(${col} AS VARCHAR)`;return `${value} AS "${col}"`;});
  const relation=name==='history'?'ANALYTICS.SNAPSHOTS.snap_employer':'ANALYTICS.MARTS.'+name;
  const filter={dim_occupation:" WHERE soc_vintage='SOC2018'",dim_geography:" WHERE geography_level='COUNTY' AND county_reference_vintage IN ('Census2020','Census2023')",dim_date:" WHERE calendar_date >= '2000-01-01' AND calendar_date < '2036-01-01'"}[name]||'';
  result[name]=`SELECT ${expressions.join(',')} FROM ${relation}${at}${filter}`;
 }
 result.metrics=fs.readFileSync(path.join(__dirname,'verify-snowflake.sql'),'utf8').replace('{snapshot}',snapshot).trim();
 return result;
}
function createReader(settings,snapshot,clock=Date.now){
 const {ReadOnlySnowflakeDriver}=require('../../semantic/source.cjs');
 const {LifecycleSnowflakeDriver}=require('../../semantic/snowflake-lifecycle.cjs');
 const sql=templates(snapshot),allowed=new Set(Object.values(sql));
 class SubjectDriver extends ReadOnlySnowflakeDriver{
  query(statement,values=[]){
   if(allowed.has(statement)&&values.length===0)return LifecycleSnowflakeDriver.prototype.query.call(this,statement,values);
   return super.query(statement,values);
  }
 }
 const driver=new SubjectDriver(settings,clock);
 return Object.freeze({read(name,...extra){if(extra.length||typeof name!=='string'||!Object.hasOwn(sql,name))return Promise.reject(Error('unknown subject template'));return driver.query(sql[name],[]);},preflight:()=>driver.preflight(),release:()=>driver.release()});
}
function queryHashes(snapshot){const sql=templates(snapshot);return {history:sha(sql.history),lineage:sha(sql.lineage)};}
module.exports={createReader,queryHashes};
