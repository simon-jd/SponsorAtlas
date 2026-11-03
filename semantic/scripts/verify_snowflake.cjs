// Explicit remote opt-in only. No successful report is produced without real REST/SQL agreement.
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');const {spawn}=require('node:child_process');const jwt=require('jsonwebtoken');
const {sourceSettings,ReadOnlySnowflakeDriver,projections}=require('../source.cjs');
const {finiteNumber,assertExactEqual,assertExactRatio,stopChild}=require('../verification.cjs');
// Every comparison is exact: counts, quantiles and medians as returned, rates as numerator / denominator.
const exact=(row,field,expected)=>{if(!row||!Object.hasOwn(row,field))throw Error('Metric field missing: '+field);try{assertExactEqual(row[field],expected);}catch{throw Error('Exact mismatch: '+field+(process.env.SEMANTIC_SNOWFLAKE_DEBUG==='1'?` ${JSON.stringify(row[field])} != ${JSON.stringify(expected)}`:''));}};
const ratio=(row,field,numerator,denominator)=>{if(!row||!Object.hasOwn(row,field))throw Error('Metric field missing: '+field);try{assertExactRatio(row[field],numerator,denominator);}catch{throw Error('Ratio mismatch: '+field);}};
const root=path.resolve(__dirname,'../..');
(async()=>{
 const settings=sourceSettings();if(settings.source!=='snowflake')throw Error('Snowflake source required');
 const work=fs.mkdtempSync(path.join(root,'.tools/semantic/snowflake-verify-'));
 const driver=new ReadOnlySnowflakeDriver(settings);let child;let fd;let evidence;const records=[];
 try{
  const preflight=await driver.preflight();
  for(const x of preflight.checks)if(x.table.startsWith('fact_')&&finiteNumber(x.rows)===0)throw Error('Empty source is not a remote acceptance dataset');
  const version=async()=>{const out={};for(const [name,template] of Object.entries(projections)){const rows=await driver.query(`SELECT COUNT(*) AS "rows",HASH_AGG(*) AS "hash" FROM (${template.replaceAll('{s}','ANALYTICS.MARTS')}) projection`,[]);out[name]=rows[0];}return out;};
  const before=await version();
  const [expected]=await driver.query('SELECT COUNT(DISTINCT case_number) AS "case_count",SUM(CASE WHEN is_certified THEN 1 ELSE 0 END) AS "certified_count",PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY annual_wage) AS "wage_p50",PERCENTILE_CONT(0.75) WITHIN GROUP(ORDER BY annual_wage) AS "wage_p75",PERCENTILE_CONT(0.9) WITHIN GROUP(ORDER BY annual_wage) AS "wage_p90" FROM ANALYTICS.MARTS.fact_lca_case',[]);
  const [posting]=await driver.query('SELECT COUNT(*) AS "value" FROM ANALYTICS.MARTS.fact_job_posting_daily WHERE is_active',[]);
  const [wage]=await driver.query("SELECT COUNT(*) AS \"estimates\",MAX(annual_median_wage) AS \"value\" FROM ANALYTICS.MARTS.fact_wage_benchmark WHERE soc_code='15-1252' AND area_type='1' AND area_code='99' AND survey_year=2025",[]);
  expected.certified_rate=finiteNumber(expected.certified_count)/finiteNumber(expected.case_count);
  exact(wage,'estimates',1);finiteNumber(wage.value);
  const secret=crypto.randomBytes(32).toString('hex');const token=jwt.sign({sub:'opt-in-remote-verification'},secret,{expiresIn:'15m'});
  const port=Number(process.env.SEMANTIC_TEST_PORT||4319);fd=fs.openSync(path.join(work,'cube.log'),'w');
  child=spawn(process.execPath,['--max-old-space-size=1024','server.cjs'],{cwd:path.join(root,'semantic'),env:{...process.env,SEMANTIC_SOURCE:'snowflake',SEMANTIC_WORK:work,CUBEJS_API_SECRET:secret,PORT:String(port)},stdio:['ignore',fd,fd]});
  const api=async(route,query)=>{const r=await fetch(`http://127.0.0.1:${port}/cubejs-api/v1/${route}`,{method:query?'POST':'GET',headers:{Authorization:token,'Content-Type':'application/json'},...(query?{body:JSON.stringify({query})}:{})});return {status:r.status,body:await r.json()};};
  let ready=false;for(let i=0;i<1800;i++){if(child.exitCode!==null)throw Error('Cube startup failed');try{if((await api('meta')).status===200){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,100));}if(!ready)throw Error('Cube readiness timeout');
  const load=async(query)=>{for(let i=0;i<100;i++){const r=await api('load',query);if(r.body.error==='Continue wait'){await new Promise(r=>setTimeout(r,250));continue;}if(r.status!==200||r.body.error)throw Error('Remote Cube load failed');records.push({query,...r});return r.body;}throw Error('Remote load timeout');};
  const metrics=['case_count','certified_rate','wage_p50','wage_p75','wage_p90'];const lca=await load({measures:metrics.map(m=>'lca.'+m)});for(const m of metrics.filter(m=>m!=='certified_rate'))exact(lca.data[0],'lca.'+m,expected[m]);ratio(lca.data[0],'lca.certified_rate',expected.certified_count,expected.case_count);if(lca.usedPreAggregations)throw Error('Exact quantiles used rollup');
  const p=await load({measures:['postings.count']});exact(p.data[0],'postings.count',posting.value);if(!p.usedPreAggregations)throw Error('Cold external posting rollup not used');
  const filters=[['soc','15-1252'],['area','1:99'],['survey_year','2025']].map(([member,value])=>({member:'wages.'+member,operator:'equals',values:[value]}));exact((await load({measures:['wages.oews_median'],filters})).data[0],'wages.oews_median',wage.value);
  const [sqlFy]=[await driver.query('SELECT fiscal_year AS "fy",COUNT(*) AS "n",SUM(CASE WHEN is_certified THEN 1 ELSE 0 END) AS "certified" FROM ANALYTICS.MARTS.fact_lca_case GROUP BY fiscal_year',[])];
  const rollup=await load({measures:['lca.case_count','lca.certified_rate'],dimensions:['lca.fiscal_year']});if(!rollup.usedPreAggregations)throw Error('Cold external LCA rollup not used');for(const row of sqlFy){const actual=rollup.data.find(r=>finiteNumber(r['lca.fiscal_year'])===finiteNumber(row.fy));if(!actual)throw Error('Missing fiscal group');exact(actual,'lca.case_count',row.n);ratio(actual,'lca.certified_rate',row.certified,row.n);}
  const dims=['employer','soc_major','soc','metro','fiscal_year'];const grouped=[];
  // Full year: 280,799 groups. Bounded by the export row cap; stops at the first short page.
  for(let offset=0;offset<1000000;offset+=50000){const r=await load({measures:['lca.case_count'],dimensions:dims.map(d=>'lca.'+d),order:Object.fromEntries(dims.map(d=>['lca.'+d,'asc'])),offset,limit:50000});grouped.push(...r.data);if(r.data.length<50000)break;}
  exact({sum:grouped.reduce((s,r)=>s+finiteNumber(r['lca.case_count']),0)},'sum',expected.case_count);
  const sqlGroups=await driver.query(`SELECT COALESCE(f.employer_id,'UNKNOWN') AS "employer",COALESCE(o.major_code,'UNKNOWN') AS "soc_major",COALESCE(f.soc_code,'UNKNOWN') AS "soc",COALESCE(CASE WHEN g.cbsa_type='Metropolitan Statistical Area' THEN g.cbsa_code END,'UNKNOWN') AS "metro",f.fiscal_year AS "fiscal_year",COUNT(DISTINCT f.case_number) AS "case_count" FROM ANALYTICS.MARTS.fact_lca_case f LEFT JOIN ANALYTICS.MARTS.dim_occupation o ON f.occupation_key=o.occupation_key LEFT JOIN ANALYTICS.MARTS.dim_geography g ON f.geography_key=g.geography_key GROUP BY 1,2,3,4,5`,[]);
  const digest=rows=>crypto.createHash('sha256').update(rows.map(row=>[...dims.map(d=>String(row[d])),String(row.case_count)].join('\u001f')).sort().join('\n')).digest('hex');
  const groupHash=digest(grouped.map(row=>Object.fromEntries([...dims,'case_count'].map(d=>[d,row['lca.'+d]]))));if(groupHash!==digest(sqlGroups))throw Error('Five-dimension grouped source/REST mismatch');
  for(const [view,measure,value] of [['lca_metrics','case_count',expected.case_count],['posting_metrics','count',posting.value],['wage_metrics','oews_median',wage.value]]){const r=await load({measures:[view+'.'+measure],...(view==='wage_metrics'?{filters:filters.map(f=>({...f,member:f.member.replace('wages.','wage_metrics.')}))}:{})});exact(r.data[0],view+'.'+measure,value);}
  const after=await version();if(JSON.stringify(before)!==JSON.stringify(after))throw Error('Live source changed during verification');
  // Optional: the local DuckDB oracle (semantic/scripts/prepare.py actual) must equal the Snowflake SQL exactly.
  let local=null;
  if(process.env.SEMANTIC_LOCAL_ORACLE){
   const o=JSON.parse(fs.readFileSync(path.resolve(root,process.env.SEMANTIC_LOCAL_ORACLE),'utf8'));
   exact(o.lca,'case_count',expected.case_count);exact(o.lca,'certified_rate',expected.certified_rate);
   if(o.lca.certified_count!==undefined)exact(o.lca,'certified_count',expected.certified_count);
   for(const m of ['wage_p50','wage_p75','wage_p90'])exact(o.lca,m,expected[m]);
   exact(o,'postings',posting.value);exact(o,'oews_median',wage.value);
   if(o.lca_groups.count!==grouped.length||o.lca_groups.sha256!==groupHash)throw Error('Local five-dimension groups differ');
   for(const g of o.fiscal_groups){const row=sqlFy.find(r=>finiteNumber(r.fy)===g.fiscal_year);if(!row)throw Error('Local fiscal group missing on Snowflake');exact(row,'n',g.case_count);exact({local_fiscal_rate:finiteNumber(row.certified)/finiteNumber(row.n)},'local_fiscal_rate',g.certified_rate);}
   if(o.fiscal_groups.length!==sqlFy.length)throw Error('Fiscal group count differs from local');
   local={oracle:path.relative(root,path.resolve(root,process.env.SEMANTIC_LOCAL_ORACLE)),status:'EXACT_MATCH',lca:o.lca,postings:o.postings,oews_median:o.oews_median,lca_groups:o.lca_groups,fiscal_groups:o.fiscal_groups};
  }
  evidence={kind:'ACTUAL_SNOWFLAKE_REAL_CUBE_REST_SQL',status:'PASS',preflight,expected,posting,wage,fiscal_groups:sqlFy,local,source_before:before,source_after:after,groups:grouped.length,groupHash,records};
 }finally{
  const cleanup=await Promise.allSettled([stopChild(child),driver.release()]);
  if(fd!==undefined)fs.closeSync(fd);
  const failure=cleanup.find(result=>result.status==='rejected');if(failure)throw failure.reason;
 }
 // An exit timeout, nonzero child exit or failed SDK destroy cannot publish PASS.
 fs.writeFileSync(path.join(work,'remote-evidence.json'),JSON.stringify(evidence,null,2));
 console.log(JSON.stringify({status:'PASS',kind:evidence.kind,work,metrics:7,dimensions:5,views:3,external_rollups:2}));
})().catch(error=>{if(process.env.SEMANTIC_SNOWFLAKE_DEBUG==='1')console.error(String(error?.message).slice(0,300));console.error('Snowflake opt-in verification failed; remote acceptance remains OPEN (details suppressed)');process.exit(1);});
