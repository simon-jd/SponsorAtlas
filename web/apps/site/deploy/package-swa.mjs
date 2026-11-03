// Assemble one self-contained prebuilt Next application from traced standalone output.
// No source database, source checkout, dependency install or fixture fallback is an input.
import {readdir,lstat,readFile,writeFile,mkdir,copyFile,chmod,realpath,symlink} from 'node:fs/promises';
import {resolve,join,relative,dirname,sep} from 'node:path';
import {createHash} from 'node:crypto';
import {VIZ_LIMITS} from '../lib/viz-limits.ts';
const hash=b=>createHash('sha256').update(b).digest('hex');
const within=(root,p)=>p===root||p.startsWith(root+sep);
export async function packageSwa(standalone,staticDirectory,vizDirectory,destination,expectedSnapshot){
 const traced=resolve(standalone),site=join(traced,'apps/site'),out=resolve(destination),viz=resolve(vizDirectory);
 if(!/^[a-f0-9]{64}$/.test(expectedSnapshot))throw Error('Reviewed snapshot SHA required');
 const originalManifest=await readFile(join(viz,'manifest.json')),manifest=JSON.parse(originalManifest);
 if(manifest.kind!=='WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT'||manifest.sourceSnapshot!==expectedSnapshot||manifest.bytes>VIZ_LIMITS.maxFrequencyBytes)throw Error('Reviewed actual projection identity required');
 for(const file of ['manifest.json','frequencies.json'])if((await lstat(join(viz,file))).mode&0o222)throw Error('Immutable input required');
 const frequency=await readFile(join(viz,'frequencies.json'));
 if(frequency.length!==manifest.bytes||hash(frequency)!==manifest.sha256)throw Error('Projection integrity mismatch');
 await mkdir(out,{recursive:false});
 const map=p=>within(site,p)?join(out,relative(site,p)):within(traced,p)?join(out,relative(traced,p)):(()=>{throw Error('Traced dependency escapes package')})();
 async function copy(source,target){
  const stat=await lstat(source);
  if(stat.isSymbolicLink()){
   const resolved=await realpath(source);if(!within(traced,resolved))throw Error('Dependency outside standalone trace');
   try{const existing=await realpath(target);if(existing===map(resolved))return;throw Error('Conflicting flattened dependency');}catch(e){if(e.code!=='ENOENT')throw e;}
   await symlink(relative(dirname(target),map(resolved)),target);return;
  }
  if(stat.isDirectory()){await mkdir(target,{recursive:true});for(const name of await readdir(source))await copy(join(source,name),join(target,name));}
  else if(stat.isFile()){await copyFile(source,target);await chmod(target,stat.mode&0o777);}
  else throw Error('Unsupported traced entry');
 }
 // Flatten apps/site into the artifact root, preserving/rebasing all internal dependency links.
 for(const name of await readdir(traced))if(!['apps','package.json'].includes(name))await copy(join(traced,name),join(out,name));
 for(const name of await readdir(site)){
  if(name==='evidence')continue;
  if(name==='node_modules'){
   await mkdir(join(out,name),{recursive:true});for(const child of await readdir(join(site,name)))await copy(join(site,name,child),join(out,name,child));
  }else await copy(join(site,name),join(out,name));
 }
 await copy(resolve(staticDirectory),join(out,'.next/static'));
 const data=join(out,'deployment-data/viz');await mkdir(data,{recursive:true});
 await writeFile(join(data,'manifest.json'),originalManifest,{mode:0o444});await writeFile(join(data,'frequencies.json'),frequency,{mode:0o444});
 const pkg=JSON.parse(await readFile(join(out,'package.json'),'utf8'));pkg.engines={...pkg.engines,node:'22.23.4'};pkg.scripts={start:'node server.js'};await writeFile(join(out,'package.json'),JSON.stringify(pkg,null,2)+'\n');
 // Next loads this at runtime. Paths resolve from the generated server's artifact-root cwd.
 await writeFile(join(out,'.env.production'),`SITE_DATA_SOURCE=parquet\nSITE_SNAPSHOT_SHA=${expectedSnapshot}\nSITE_SOURCE_VERSION=${expectedSnapshot}\nSITE_PACKAGED_PROJECTION=1\nCUBE_REVALIDATE_SECONDS=60\n`,{mode:0o444});
 const receipt={kind:'ACTUAL_PUBLIC_STANDALONE_ARTIFACT',snapshot:expectedSnapshot,projectionSHA256:manifest.sha256,manifestSHA256:hash(originalManifest),projectionBytes:frequency.length,runtimePath:'<artifact-root>/deployment-data/viz',sourceConfiguration:'<artifact-root>/.env.production',node:'22.23.4',entry:'server.js'};
 await writeFile(join(out,'deployment-data/ancestry.json'),JSON.stringify(receipt,null,2),{mode:0o444});return receipt;
}
if(process.argv[1]===new URL(import.meta.url).pathname){if(process.argv.length!==7)throw Error('standalone static viz NEW-output expectedSnapshotSHA');console.log(JSON.stringify(await packageSwa(...process.argv.slice(2)),null,2));}
