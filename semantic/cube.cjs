const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {sourceSettings,ReadOnlySnowflakeDriver,modelRepository}=require('./source.cjs');
const {ExternalDuckDBDriver}=require('./external.cjs');
const settings=sourceSettings();
const h3=require('./h3.cjs');
if(settings.source==='parquet')module.exports=require('./parquet.cjs');
else{
 if(!process.env.SEMANTIC_WORK||!process.env.CUBEJS_API_SECRET)throw Error('SEMANTIC_WORK and server-side CUBEJS_API_SECRET required');
 const work=path.resolve(process.env.SEMANTIC_WORK);fs.mkdirSync(work,{recursive:true});
 const identity=crypto.createHash('sha256').update(settings.config.account+':'+settings.start+':ANALYTICS.MARTS').digest('hex');
 const marker=path.join(work,'source-identity.txt');
 if(fs.existsSync(marker)&&fs.readFileSync(marker,'utf8')!==identity)throw Error('Snowflake source changed: use a fresh SEMANTIC_WORK directory');
 fs.writeFileSync(marker,identity);
 module.exports={devServer:false,apiSecret:process.env.CUBEJS_API_SECRET,scheduledRefreshTimer:false,preAggregationsSchema:'semantic_preagg',contextToApiScopes:()=>['data','meta'],
  repositoryFactory:()=>modelRepository('snowflake'),
  driverFactory:()=>new ReadOnlySnowflakeDriver(settings),
  externalDbType:'duckdb',externalDriverFactory:()=>new ExternalDuckDBDriver(work),
  orchestratorOptions:{queryCacheOptions:{queueOptions:{concurrency:1}},preAggregationsOptions:{externalRefresh:false,queueOptions:{concurrency:1}}},
  queryRewrite:query=>{
   if(Date.now()>=settings.cutoff)throw Error('Snowflake trial query cutoff reached');
   if(h3.validateQuery(query,process.env.SEMANTIC_H3_ENABLED==='1'))return query;
   const members=[...(query.measures||[]),...(query.dimensions||[]),...(query.timeDimensions||[]).map(x=>x.dimension)];
   const visit=f=>{if(f.member)members.push(f.member);for(const k of ['and','or'])for(const c of f[k]||[])visit(c);};for(const f of query.filters||[])visit(f);
   if(members.some(m=>typeof m!=='string'||!/^((lca|postings|wages)|((lca|posting|wage)_metrics))\.[a-z_0-9]+$/.test(m))||new Set(members.map(m=>m.split('.')[0])).size>1)throw Object.assign(new Error('Query one named semantic grain at a time'),{type:'UserError'});
   return query;
  },
  // Never log driver options/errors containing authentication material.
  logger:(message)=>{if(/error/i.test(message))console.error('Cube Snowflake query failed');},
 };
}
