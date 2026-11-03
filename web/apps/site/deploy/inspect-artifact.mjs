// Measure only a physically assembled artifact; validate every symlink stays inside it.
import {readdir,lstat,readFile,realpath} from 'node:fs/promises';
import {resolve,join,relative,sep} from 'node:path';
import {createHash} from 'node:crypto';
export async function inspectArtifact(directory){
 const root=resolve(directory),files=[],links=[];let totalBytes=0;
 async function walk(dir){for(const name of await readdir(dir)){const p=join(dir,name),s=await lstat(p);
  if(s.isDirectory())await walk(p);
  else if(s.isSymbolicLink()){const target=await realpath(p);if(!target.startsWith(root+sep))throw Error('Artifact symlink escapes');links.push({path:relative(root,p),target:relative(root,target)});}
  else if(s.isFile()){totalBytes+=s.size;files.push({path:relative(root,p),bytes:s.size,sha256:createHash('sha256').update(await readFile(p)).digest('hex')});}
  else throw Error('Unsupported artifact entry');
 }}
 await walk(root);if(totalBytes>250000000)throw Error('Artifact exceeds250MB');
 files.sort((a,b)=>a.path.localeCompare(b.path));
 const ancestry=JSON.parse(await readFile(join(root,'deployment-data/ancestry.json'),'utf8'));
 return {scope:'ASSEMBLED_STANDALONE_ARTIFACT_NOT_HOSTED_SWA',totalBytes,limitBytes:250000000,fileCount:files.length,symlinkCount:links.length,fileManifestSHA256:createHash('sha256').update(JSON.stringify(files)).digest('hex'),ancestry,files,links};
}
if(process.argv[1]===new URL(import.meta.url).pathname){if(process.argv.length!==3)throw Error('Assembled artifact directory required');console.log(JSON.stringify(await inspectArtifact(process.argv[2]),null,2));}
