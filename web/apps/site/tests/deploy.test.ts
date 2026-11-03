import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer,request} from 'node:http';
const require=createRequire(import.meta.url);
const {bindSettings}=require('../../../../semantic/bind.cjs');
test('container binding is explicit, defaults loopback, rejects invalid host/port',()=>{
 assert.deepEqual(bindSettings({}),{host:'127.0.0.1',port:4000});assert.deepEqual(bindSettings({SEMANTIC_CONTAINER:'1',SEMANTIC_BIND_HOST:'0.0.0.0',PORT:'4001'}),{host:'0.0.0.0',port:4001});
 for(const e of [{SEMANTIC_BIND_HOST:'0.0.0.0'},{SEMANTIC_BIND_HOST:'::'},{SEMANTIC_CONTAINER:'true'},{PORT:'80'},{PORT:'4000x'},{PORT:'65536'}])assert.throws(()=>bindSettings(e));
});
test('validated ingress bind accepts a real HTTP loopback connection without changing auth behavior',async()=>{
 const server=createServer((req,res)=>res.writeHead(req.headers.authorization==='controlled'?200:403).end());
 const bind=bindSettings({SEMANTIC_CONTAINER:'1',SEMANTIC_BIND_HOST:'0.0.0.0'});
 await new Promise<void>(ok=>server.listen(0,bind.host,ok));
 try{const address=server.address() as {port:number};const response=await fetch(`http://127.0.0.1:${address.port}`);assert.equal(response.status,403);}finally{await new Promise<void>(ok=>server.close(()=>ok()));}
});

test('deployment staging preserves public bytes/ancestry and rewrites only derived manifest destinations',async()=>{
 const {mkdtemp,mkdir,writeFile,readFile,rm}=await import('node:fs/promises');const {tmpdir}=await import('node:os');const {join}=await import('node:path');const {createHash}=await import('node:crypto');const {stage}=await import('../deploy/stage.mjs');
 const root=await mkdtemp(join(tmpdir(),'sponsoratlas-stage-'));const hash=(b:string)=>createHash('sha256').update(b).digest('hex');
 try{
  const source=join(root,'public'),map=join(root,'map'),output=join(root,'stage');await mkdir(source);await mkdir(map);
  const entries=[];for(const name of ['lca','postings','wages']){const path=join(source,name+'.parquet'),value='CONTROLLED_TEST_BYTES_'+name;await writeFile(path,value,{mode:0o444});entries.push({path,size:Buffer.byteLength(value),sha256:hash(value)});}
  const manifest=JSON.stringify({kind:'ACTUAL_PUBLIC_MARTS',exports:entries});await writeFile(join(source,'export-evidence.json'),manifest);const frequency='{"columns":[],"rows":[]}';await writeFile(join(map,'frequencies.json'),frequency,{mode:0o444});await writeFile(join(map,'manifest.json'),JSON.stringify({kind:'WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT',sourceSnapshot:entries[0].sha256,sha256:hash(frequency),bytes:Buffer.byteLength(frequency)}));
  const receipt=await stage(source,map,output);assert.equal(receipt.sourceManifestSHA256,hash(manifest));assert.equal(await readFile(join(source,'export-evidence.json'),'utf8'),manifest);
  const derived=JSON.parse(await readFile(join(output,'snapshot/export-evidence.json'),'utf8'));assert.equal(derived.exports[0].path,'/app/snapshot/lca.parquet');assert.equal(derived.exports[0].sha256,entries[0].sha256);assert.equal(derived.exports[0].originalPath,entries[0].path);
  await assert.rejects(stage(source,map,output),/EEXIST/);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('source directory defaults preserve local work and explicit container source is required',()=>{
 const {snapshotDirectory}=require('../../../../semantic/snapshot.cjs');
 assert.equal(snapshotDirectory({SEMANTIC_WORK:'/controlled/local'}),'/controlled/local');
 assert.equal(snapshotDirectory({SEMANTIC_WORK:'/controlled/work',SEMANTIC_SNAPSHOT:'/controlled/source'}),'/controlled/source');
 assert.throws(()=>snapshotDirectory({SEMANTIC_WORK:'/controlled/work',SEMANTIC_SNAPSHOT:'relative'}),/absolute/);
 assert.throws(()=>snapshotDirectory({SEMANTIC_SOURCE:'parquet',SEMANTIC_CONTAINER:'1',SEMANTIC_WORK:'/controlled/work'}),/separate immutable/);
});

test('SWA artifact physically contains actual projection, runtime identity and rebased internal dependencies',async()=>{
 const fs=await import('node:fs/promises'),path=await import('node:path'),os=await import('node:os'),crypto=await import('node:crypto');
 const {packageSwa}=await import('../deploy/package-swa.mjs');const {inspectArtifact}=await import('../deploy/inspect-artifact.mjs');
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'sponsoratlas-package-'));
 try{
  const traced=path.join(root,'traced'),site=path.join(traced,'apps/site'),viz=path.join(root,'viz'),staticDir=path.join(root,'static'),output=path.join(root,'artifact');
  await fs.mkdir(path.join(site,'node_modules'),{recursive:true});await fs.mkdir(path.join(traced,'node_modules/next'),{recursive:true});await fs.mkdir(viz);await fs.mkdir(staticDir);await fs.mkdir(path.join(site,'.next'));
  await fs.writeFile(path.join(traced,'node_modules/next/index.js'),'module.exports=42');await fs.symlink('../../../node_modules/next',path.join(site,'node_modules/next'));
  await fs.writeFile(path.join(site,'server.js'),'// controlled trace');await fs.writeFile(path.join(site,'package.json'),JSON.stringify({name:'controlled',engines:{node:'24.22.1'}}));await fs.writeFile(path.join(staticDir,'chunk.js'),'globalThis.test=1');
  const frequency=Buffer.from('{"columns":[],"rows":[]}'),snapshot='a'.repeat(64);const sha=crypto.createHash('sha256').update(frequency).digest('hex');
  await fs.writeFile(path.join(viz,'frequencies.json'),frequency,{mode:0o444});await fs.writeFile(path.join(viz,'manifest.json'),JSON.stringify({kind:'WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT',sourceSnapshot:snapshot,sha256:sha,bytes:frequency.length}),{mode:0o444});
  await assert.rejects(packageSwa(traced,staticDir,viz,output,'b'.repeat(64)),/identity/);
  const receipt=await packageSwa(traced,staticDir,viz,output,snapshot);assert.equal(receipt.projectionSHA256,sha);
  assert.equal(await fs.realpath(path.join(output,'node_modules/next')),path.join(output,'node_modules/next'));
  assert.match(await fs.readFile(path.join(output,'.env.production'),'utf8'),/SITE_PACKAGED_PROJECTION=1/);
  const measured=await inspectArtifact(output);assert.equal(measured.ancestry.snapshot,snapshot);assert.ok(measured.totalBytes>frequency.length);assert.ok(measured.files.some((x:any)=>x.path==='deployment-data/viz/frequencies.json'));
 }finally{await fs.rm(root,{recursive:true,force:true});}
});
