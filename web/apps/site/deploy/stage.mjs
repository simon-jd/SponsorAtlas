// Prepare only already-reviewed PUBLIC projections. Never opens the source facts DB.
// Usage: node deploy/stage.mjs <public-snapshot-directory> <map-projection-directory> <new-stage-directory>
import {mkdir,readFile,writeFile,copyFile,chmod,stat} from 'node:fs/promises';
import {resolve,join,basename} from 'node:path';
import {createHash} from 'node:crypto';
import {VIZ_LIMITS} from '../lib/viz-limits.ts';
const sha=b=>createHash('sha256').update(b).digest('hex');
export async function stageCube(snapshot,destination){
 const dir=resolve(destination);await mkdir(dir,{recursive:false});
 const sourceManifest=await readFile(join(snapshot,'export-evidence.json'));
 const manifest=JSON.parse(sourceManifest);
 if(!['ACTUAL_PUBLIC_MARTS','ACTUAL_SNOWFLAKE_PUBLIC_MARTS'].includes(manifest.kind)||manifest.exports.length!==3)throw Error('Reviewed public snapshot required');
 const deployed={...manifest,exports:[]};
 for(const name of ['lca','postings','wages']){
  const source=join(resolve(snapshot),name+'.parquet');const entry=manifest.exports.find(x=>x.path===source);
  if(!entry||entry.size>100000000||(await stat(source)).size!==entry.size||(await stat(source)).mode&0o222)throw Error('Invalid immutable public projection');
  const data=await readFile(source);if(data.length!==entry.size||sha(data)!==entry.sha256)throw Error('Projection integrity');
  const target=join(dir,name+'.parquet');await copyFile(source,target);await chmod(target,0o444);
  deployed.exports.push({...entry,path:'/app/snapshot/'+name+'.parquet',originalPath:entry.path});
 }
 await writeFile(join(dir,'export-evidence.json'),JSON.stringify(deployed,null,2),{mode:0o444});
 const ancestry={sourceManifestSHA256:sha(sourceManifest),kind:'DERIVED_DEPLOYMENT_MANIFEST_UNCHANGED_PUBLIC_BYTES',bytes:deployed.exports.reduce((n,x)=>n+x.size,0)};
 await writeFile(join(dir,'ancestry.json'),JSON.stringify(ancestry,null,2));return {deployed,ancestry};
}
export async function stage(snapshot,map,destination){
 const dir=resolve(destination);await mkdir(dir,{recursive:false});
 const {deployed,ancestry:cubeAncestry}=await stageCube(snapshot,join(dir,'snapshot'));await mkdir(join(dir,'viz'));
 const mapManifest=JSON.parse(await readFile(join(map,'manifest.json'),'utf8'));
 if(mapManifest.kind!=='WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT'||mapManifest.sourceSnapshot!==deployed.exports.find(x=>basename(x.path)==='lca.parquet').sha256||mapManifest.bytes>VIZ_LIMITS.maxFrequencyBytes)throw Error('Map ancestry mismatch');
 const mapStat=await stat(join(map,'frequencies.json'));if(mapStat.size!==mapManifest.bytes||(mapStat.mode&0o222))throw Error('Immutable map required');
 const bytes=await readFile(join(map,'frequencies.json'));
 if(bytes.length!==mapManifest.bytes||sha(bytes)!==mapManifest.sha256)throw Error('Map integrity');
 await writeFile(join(dir,'viz/frequencies.json'),bytes,{mode:0o444});
 await writeFile(join(dir,'viz/manifest.json'),JSON.stringify(mapManifest),{mode:0o444});
 const ancestry={sourceManifestSHA256:cubeAncestry.sourceManifestSHA256,lcaSHA256:mapManifest.sourceSnapshot,mapSHA256:mapManifest.sha256,kind:'DERIVED_DEPLOYMENT_MANIFEST_UNCHANGED_PUBLIC_BYTES',bytes:deployed.exports.reduce((n,x)=>n+x.size,0)+bytes.length};
 await writeFile(join(dir,'ancestry.json'),JSON.stringify(ancestry,null,2));return ancestry;
}
if(process.argv[1]===new URL(import.meta.url).pathname){if(process.argv.length!==5)throw Error('snapshot map new-destination required');console.log(process.argv[2]==='--cube'?await stageCube(process.argv[3],process.argv[4]):await stage(...process.argv.slice(2)));}
