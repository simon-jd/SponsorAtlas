// Fixed, pinned native observations. Export artifacts are never config attestations.
const fs=require('node:fs'),path=require('node:path');
const {schema,digest,historyBytes,sha}=require('./content.cjs');
const {createReader,queryHashes}=require('./snowflake-subject.cjs');
async function observe(reader,snapshot,{exportOnly=false}={}){
 const preflight=await reader.preflight();
 const identities=await reader.read('identity');if(identities.length!==1)throw Error('native identity missing');
 const source=identities[0];for(const key of ['account','region','user','role'])if(!source[key])throw Error('native source missing');
 const lineages=await reader.read('lineage');if(lineages.length!==1)throw Error('native lineage missing');
 const lineage=JSON.parse(lineages[0].manifest_json);
 for(const key of ['registry_sha256','model_sha256','xref_sha256','input_records_sha256','input_occurrences_sha256'])if(!/^[0-9a-f]{64}$/.test(lineage[key]||''))throw Error('native lineage digest missing');
 for(const key of ['namespace','input_kind','acceptance_status'])if(!lineage[key])throw Error('native lineage field missing');
 const historyRows=await reader.read('history'),bytes=historyBytes(historyRows);
 if(!historyRows.length)throw Error('native history empty');
 const receipt={kind:'ACTUAL_SNOWFLAKE_SQL',source,snapshot,lineage,history:{...digest('history',historyRows),bytes_sha256:sha(bytes)},queries:queryHashes(snapshot),snapshot_mode:'Snowflake Time Travel',preflight};
 if(!exportOnly){
  const metrics=await reader.read('metrics');if(metrics.length!==1)throw Error('native metrics missing');receipt.metrics=metrics[0];
  if(!/^\d+$/.test(String(receipt.metrics.case_count))||BigInt(receipt.metrics.case_count)>BigInt(Number.MAX_SAFE_INTEGER))throw Error('count precision');receipt.metrics.case_count=Number(receipt.metrics.case_count);
  receipt.content={};for(const name of Object.keys(schema).filter(n=>n!=='history'))receipt.content[name]=digest(name,await reader.read(name));
  if(receipt.content.fact_lca_case.rows!==receipt.metrics.case_count)throw Error('native grain mismatch');
 }
 return {receipt,bytes};
}
async function main(){
 const snapshot=process.argv[2],output=process.argv[3];queryHashes(snapshot);
 const {sourceSettings}=require('../../semantic/source.cjs');const settings=sourceSettings();if(settings.source!=='snowflake')throw Error('native Snowflake required');
 const reader=createReader(settings,snapshot);let result;
 try{result=await observe(reader,snapshot,{exportOnly:!!output});}finally{await reader.release();}
 if(output){
  const receipt={...result.receipt,kind:'ACTUAL_SNOWFLAKE_HISTORY_EXPORT',relation:'ANALYTICS.SNAPSHOTS.snap_employer'};
  fs.mkdirSync(output,{mode:0o700});fs.writeFileSync(path.join(output,'history.jsonl'),result.bytes,{flag:'wx',mode:0o600});fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2)+'\n',{flag:'wx',mode:0o600});
  process.stdout.write(JSON.stringify({kind:receipt.kind,output}));
 }else process.stdout.write(JSON.stringify(result.receipt));
}
if(require.main===module)main().catch(()=>{console.error('Native Snowflake gate failed; no parity receipt');process.exitCode=1;});
module.exports={observe};
