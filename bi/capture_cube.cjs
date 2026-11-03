// A new genuine REST execution over a derived, checksum-identical snapshot.
// No token or key is persisted. Child lifecycle is bounded and always closed.
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..');
const requireSemantic = createRequire(path.join(root, 'semantic/package.json'));
const jwt = requireSemantic('jsonwebtoken');
const work = fs.mkdtempSync(path.join(__dirname, '.work/cube-'));
const source = path.join(root, '.tools/semantic/actual');
const manifestBytes = fs.readFileSync(path.join(source, 'export-evidence.json'));
const manifest = JSON.parse(manifestBytes);
manifest.derived_from_manifest_sha256 = crypto.createHash('sha256').update(manifestBytes).digest('hex');
for (const e of manifest.exports) {
  const file = path.basename(e.path);
  // Symlink only approved immutable public files; no facts DB copy.
  fs.symlinkSync(path.join(source, file), path.join(work, file));
  e.path = path.join(work, file);
}
fs.writeFileSync(path.join(work, 'export-evidence.json'), JSON.stringify(manifest));
const secret = crypto.randomBytes(32).toString('hex');
const token = jwt.sign({sub:'SponsorAtlas-BI-reconciliation'},secret,{expiresIn:'5m'});
const logPath = path.join(__dirname, 'evidence/cube.log');
const log = fs.openSync(logPath, 'w');
const service = spawn(process.execPath, ['--max-old-space-size=1024','server.cjs'], {
  cwd:path.join(root,'semantic'),
  env:{...process.env, SEMANTIC_SOURCE:'parquet', SEMANTIC_WORK:work,
    CUBEJS_API_SECRET:secret, PORT:'4319'}, stdio:['ignore',log,log]
});
const records=[];
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function api(route,query) {
  const r=await fetch('http://127.0.0.1:4319/cubejs-api/v1/'+route,{
    method:query?'POST':'GET', headers:{Authorization:token,'Content-Type':'application/json'},
    ...(query?{body:JSON.stringify({query})}:{}),signal:AbortSignal.timeout(10000)});
  return {status:r.status,body:await r.json()};
}
(async()=>{
  try {
    let ready=false;
    for(let i=0;i<200;i++) {
      if(service.exitCode!==null)throw Error('Cube startup failed; see evidence/cube.log');
      try {if((await api('meta')).status===200){ready=true;break;}} catch {}
      await delay(100);
    }
    if(!ready)throw Error('Cube readiness timed out');
    const queries=JSON.parse(fs.readFileSync(path.join(__dirname,'queries.json')));
    for(const [metric,query] of Object.entries(queries)) {
      let result;
      for(let i=0;i<100;i++) {
        result=await api('load',query);
        if(result.body.error!=='Continue wait')break;
        await delay(100);
      }
      if(result.status!==200||result.body.error)throw Error('Cube REST load failed: '+metric);
      records.push({metric,query,...result});
    }
    fs.writeFileSync(path.join(__dirname,'evidence/cube-rest.json'),JSON.stringify({
      origin:'ACTUAL_CUBE_1.8_REST',executed_at:new Date().toISOString(),
      manifest_sha256:manifest.derived_from_manifest_sha256,
      queries_sha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname,'queries.json'))).digest('hex'),records
    },null,2)+'\n');
    console.log('Actual Cube REST: five queries captured');
  } finally {
    service.kill('SIGTERM');
    const timeout=setTimeout(()=>service.kill('SIGKILL'),5000);
    if(service.exitCode===null)await new Promise(resolve=>service.once('exit',resolve));
    clearTimeout(timeout);fs.closeSync(log);
    if(fs.existsSync(path.join(work,'resource.json')))fs.copyFileSync(path.join(work,'resource.json'),path.join(__dirname,'evidence/cube-resource.json'));
    fs.rmSync(work,{recursive:true,force:true});
    if(service.exitCode!==0)throw Error('Cube did not exit cleanly');
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
