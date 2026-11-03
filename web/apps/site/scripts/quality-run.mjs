// A finite local CI entry point. The outer reviewed monitor owns orphan cleanup.
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {readFileSync,writeFileSync,mkdirSync,cpSync} from 'node:fs';
import {resolve} from 'node:path';
import {createServer} from 'node:net';
import {fixtureServer} from '../tests/fixture-server.mjs';
import {prepareFixture} from './quality-fixture.mjs';
const [mode,phase,...args]=process.argv.slice(2);
if(!['fixture','actual'].includes(mode)||!['budget','visual','lhci','smoke','rum','misconfiguration'].includes(phase))throw Error('mode fixture|actual; phase budget|visual|lhci|smoke|rum');
const runId=process.env.QUALITY_RUN_ID||'';
if(runId&&!/^[a-z0-9][a-z0-9-]{0,63}$/.test(runId))throw Error('Invalid QUALITY_RUN_ID');
const evidenceRoot=runId?resolve('evidence/quality-runs',runId):null;
const root=resolve('../../..'),children=[],secret=randomBytes(40).toString('hex');let fixture;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function free(port){const s=createServer();await new Promise((ok,no)=>{s.once('error',no);s.listen(port,'127.0.0.1',ok);});await new Promise(ok=>s.close(ok));}
function run(cmd,args,env={},cwd){const c=spawn(cmd,args,{stdio:'inherit',env:{...process.env,...env},...(cwd?{cwd}:{})});children.push(c);return c;}
async function ready(url){for(let i=0;i<200;i++){try{const r=await fetch(url);if(r.status<500)return;}catch{}if(children.some(c=>c.exitCode!==null))throw Error('Service exited');await pause(100);}throw Error('Readiness timeout');}
try{
 await free(3100);await free(4100);let version;
 if(mode==='fixture'){await prepareFixture(resolve('.next'));version='quality-fixture-v1';fixture=fixtureServer(secret);await new Promise(ok=>fixture.listen(4100,'127.0.0.1',ok));}
 else{version=JSON.parse(readFileSync(resolve(root,'.tools/viz/worksite-fy2025/manifest.json'),'utf8')).sourceSnapshot;run('bash',[resolve(root,'semantic/run.sh'),'serve','parquet','actual'],{CUBEJS_API_SECRET:secret,PORT:'4100'});await ready('http://127.0.0.1:4100/cubejs-api/v1/meta');}
 cpSync('.next/static','.next/standalone/apps/site/.next/static',{recursive:true});
 run(process.env.QUALITY_STANDALONE_NODE||process.execPath,['.next/standalone/apps/site/server.js'],{HOSTNAME:'127.0.0.1',PORT:'3100',...(mode==='actual'?{SITE_VIZ_DIRECTORY:resolve(root,'.tools/viz/worksite-fy2025')}:{SITE_VIZ_DIRECTORY:undefined}),CUBEJS_API_SECRET:secret,CUBE_REST_URL:'http://127.0.0.1:4100/cubejs-api/v1/load',SITE_DATA_SOURCE:mode==='actual'?'parquet':'fixture',SITE_SOURCE_VERSION:version,SITE_SNAPSHOT_SHA:version,CUBE_REVALIDATE_SECONDS:'60',RUM_ENABLED:'1',RUM_ORIGIN:'http://127.0.0.1:3100',RUM_STORAGE:phase==='misconfiguration'?'unset':'local',RUM_QA_ONLY:'1',RUM_LOCAL_DIR:evidenceRoot?resolve(evidenceRoot,'rum',`${mode}-${phase}`):resolve(`evidence/quality-rum-local/${mode}-${phase}`)});
 await ready('http://127.0.0.1:3100/en');
 let c;
 if(phase==='lhci'){
  const [locale='en',theme='light']=args;
  if(!['en','zh'].includes(locale)||!['light','dark'].includes(theme))throw Error('Invalid matrix');
  const html=await(await fetch('http://127.0.0.1:3100/en')).text();const id=/data-employer-link="([^"]+)"/.exec(html)?.[1];if(!id)throw Error('No employer');
  const config=JSON.parse(readFileSync('lighthouserc.json','utf8'));
  config.ci.collect.url=[`/${locale}`,`/${locale}/employers/${id}`,`/${locale}/compare?ids=${id}`,`/${locale}/map`].map(p=>'http://127.0.0.1:3100'+p);
  config.ci.collect.settings.extraHeaders={Cookie:`sa-theme=${theme}`};
  const lhciDir=evidenceRoot?resolve(evidenceRoot,'lhci',`${mode}-${locale}-${theme}`):null;
  config.ci.upload.outputDir=lhciDir?resolve(lhciDir,'reports'):`evidence/quality-lhci/${mode}-${locale}-${theme}`;
  const configPath=lhciDir?resolve(lhciDir,'config.json'):'evidence/quality-lhci/config.json';
  mkdirSync(lhciDir||'evidence/quality-lhci',{recursive:true});writeFileSync(configPath,JSON.stringify(config));
  if(evidenceRoot){
   const workDir=resolve(evidenceRoot,'lhci-work',`${locale}-${theme}`);
   mkdirSync(workDir,{recursive:true});
   c=run(process.execPath,[resolve('node_modules/@lhci/cli/src/cli.js'),'autorun',`--config=${configPath}`],{},workDir);
  }else c=run('pnpm',['exec','lhci','autorun','--config=evidence/quality-lhci/config.json']);
 }else c=run('pnpm',['exec','playwright','test','--config',phase==='visual'?'visual.playwright.config.ts':'quality.playwright.config.ts',...(phase==='visual'?[]:['--grep',phase]),...(evidenceRoot?['--output',resolve(evidenceRoot,'test-results',`${mode}-${phase}`)]:[]),...args],{QUALITY_MODE:mode,...(evidenceRoot?{QUALITY_EVIDENCE_DIRECTORY:resolve(evidenceRoot,'receipts',`${mode}-${phase}`)}:{})});
 process.exitCode=await new Promise(ok=>c.once('exit',code=>ok(code??1)));
}finally{
 for(const c of children.reverse())if(c.exitCode===null)c.kill('SIGTERM');
 if(fixture)await new Promise(ok=>fixture.close(ok));
 await Promise.all(children.map(async c=>{for(let i=0;i<40&&c.exitCode===null;i++)await pause(50);if(c.exitCode===null)c.kill('SIGKILL');}));
}
