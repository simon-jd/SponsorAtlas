// Independent local writable preaggregation storage. It has no source file access.
const path=require('node:path');
const fs=require('node:fs');
const {DuckDBDriver}=require('@cubejs-backend/duckdb-driver');
class ExternalDuckDBDriver extends DuckDBDriver{
 constructor(work,logger=()=>{}){
  const scratch=path.join(work,'external-scratch');fs.mkdirSync(scratch,{recursive:true});
  const q=x=>"'"+x.replaceAll("'","''")+"'";
  super({databasePath:path.join(scratch,'preaggregations.duckdb'),initSql:`SET memory_limit='512MB'; SET threads=1; SET max_temp_directory_size='256MB'; SET temp_directory=${q(path.join(scratch,'tmp'))}; SET enable_external_access=false;`});
  this.trace=logger;
 }
 async query(sql,values=[],options){this.trace(sql);return super.query(sql,values,options);}
 async testConnection(){await super.testConnection();const [r]=await this.query("SELECT current_setting('threads') AS threads,current_setting('enable_external_access') AS external_access,current_setting('memory_limit') AS memory_limit,current_setting('max_temp_directory_size') AS temp_limit",[]);if(Number(r.threads)!==1||r.external_access!==false||r.memory_limit!=='488.2 MiB'||r.temp_limit!=='244.1 MiB')throw Error('External scratch must be bounded and source-independent');}
}
module.exports={ExternalDuckDBDriver};
