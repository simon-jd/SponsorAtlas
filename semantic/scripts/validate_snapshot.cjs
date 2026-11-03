// Fixed native Cube driver reads Parquet independently of the Snowflake export connector.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {assertMetric,assertExactEqual,finiteNumber}=require('../verification.cjs');
process.env.CUBEJS_DB_DUCKDB_MEMORY_LIMIT='512MB';
const {DuckDBDriver}=require('@cubejs-backend/duckdb-driver');
(async()=>{
 const work=path.resolve(process.argv[2]);const manifest=JSON.parse(fs.readFileSync(path.join(work,'export-evidence.json')));const oracle=manifest.oracle;
 const sources=['lca','postings','wages'].map(name=>({name,file:path.join(work,name+'.parquet')}));
 const checksums={};for(const {name,file} of sources){const expected=manifest.exports.find(x=>path.basename(x.path)===name+'.parquet');const hash=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');if(!expected||expected.sha256!==hash||expected.size!==fs.statSync(file).size||(fs.statSync(file).mode&0o222))throw Error('Snapshot integrity failed');checksums[name]=hash;}
 const quote=x=>"'"+x.replaceAll("'","''")+"'";
 const driver=new DuckDBDriver({databasePath:':memory:',initSql:`SET threads=1; SET max_temp_directory_size='256MB'; SET temp_directory=${quote(path.join(work,'validation-tmp'))}; SET allowed_paths=[${sources.map(s=>quote(s.file)).join(',')}]; SET enable_external_access=false;`});
 try{
  const [lca]=await driver.query(`SELECT COUNT(DISTINCT case_number) case_count,SUM(CASE WHEN is_certified THEN 1 ELSE 0 END) certified_count,PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY annual_wage) wage_p50,PERCENTILE_CONT(0.75) WITHIN GROUP(ORDER BY annual_wage) wage_p75,PERCENTILE_CONT(0.9) WITHIN GROUP(ORDER BY annual_wage) wage_p90 FROM read_parquet(${quote(sources[0].file)})`,[]);
  const [p]=await driver.query(`SELECT COUNT(*) AS "value" FROM read_parquet(${quote(sources[1].file)}) WHERE is_active`,[]);
  const [w]=await driver.query(`SELECT CASE WHEN COUNT(*)=1 THEN MAX(annual_median_wage) END AS "value" FROM read_parquet(${quote(sources[2].file)}) WHERE soc='15-1252' AND area='1:99' AND survey_year=2025`,[]);
  // Counts and the certified rate (numerator / denominator on both sides) must match exactly.
  assertExactEqual(lca.case_count,oracle.lca.case_count);
  if(finiteNumber(lca.case_count)===0)assertMetric({certified_rate:null},'certified_rate',oracle.lca.certified_rate);
  else{assertExactEqual(lca.certified_count,oracle.lca.certified_count);assertExactEqual(finiteNumber(lca.certified_count)/finiteNumber(lca.case_count),oracle.lca.certified_rate);}
  for(const m of ['wage_p50','wage_p75','wage_p90'])assertMetric(lca,m,oracle.lca[m]);
  assertMetric(p,'value',oracle.postings);assertMetric(w,'value',oracle.oews_median);
  for(const {name,file} of sources){const [r]=await driver.query(`SELECT COUNT(*) n FROM read_parquet(${quote(file)})`,[]);assertMetric(r,'n',manifest.exports.find(x=>path.basename(x.path)===name+'.parquet').rows);}
  const [engine]=await driver.query('SELECT version() AS engine',[]);console.log(JSON.stringify({all_seven_match:true,engine:engine.engine,checksums,kind:'REAL_NATIVE_PARQUET_SQL_VALIDATION'}));
 }finally{await driver.release();}
})().catch(error=>{if(process.env.SEMANTIC_SNAPSHOT_DEBUG==='1')console.error(error);console.error('Native snapshot verification failed');process.exit(1);});
