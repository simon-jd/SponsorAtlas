import {test,expect} from '@playwright/test';
import {createServer} from 'node:http';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {measureScripts,accounting,JS_BUDGET} from './budget.ts';
function noise(length:number){let state=123456789;return Array.from({length},()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'[state%62]}).join('');}
test('accounting: streamed executable inline + distinct same-path responses and inert exclusions',async({page})=>{
 const red=process.env.QUALITY_INLINE_RED==='1';const payload=noise(red?360000:12000);
 const server=createServer((req,res)=>{
  if(req.url?.startsWith('/same.js?')){res.setHeader('Content-Type','text/javascript');res.end(`globalThis.externalCalls=(globalThis.externalCalls||0)+1;globalThis.externalValue=${JSON.stringify(req.url)};`);return;}
  res.setHeader('Content-Type','text/html; charset=utf-8');res.write('<!doctype html><h1>Controlled inline budget probe</h1><script src="/same.js?v=1"></script><script src="/same.js?v=2"></script>');
  res.write('<script>globalThis.streamed=1</script>');
  setTimeout(()=>res.end(`<script>globalThis.inlineValue="${payload}";document.currentScript.remove()</script><script type="application/json">{"inert":"${noise(1000)}"}</script><script type="module">globalThis.moduleRan=1</script>`),25);
 });
 await new Promise<void>(ok=>server.listen(0,'127.0.0.1',ok));
 try{
  const port=(server.address() as {port:number}).port;const result=await measureScripts(page,`http://127.0.0.1:${port}/`);
  expect(await page.evaluate(()=>({external:(globalThis as any).externalCalls,streamed:(globalThis as any).streamed,module:(globalThis as any).moduleRan,length:(globalThis as any).inlineValue.length}))).toEqual({external:2,streamed:1,module:1,length:payload.length});
  expect(result.external).toHaveLength(2);expect(result.external.map(x=>x.url)).toEqual([`http://127.0.0.1:${port}/same.js?v=1`,`http://127.0.0.1:${port}/same.js?v=2`]);expect(result.inline).toHaveLength(3);expect(result.excluded.map(x=>x.type)).toEqual(['application/json']);
  const evidenceDir=process.env.QUALITY_EVIDENCE_DIRECTORY||'evidence/quality-budget';
  await mkdir(evidenceDir,{recursive:true});await writeFile(resolve(evidenceDir,process.env.QUALITY_EVIDENCE_DIRECTORY?'inline-accounting.json':`fix1-inline-${red?'red':'green'}.json`),JSON.stringify({scope:'CONTROLLED_REAL_HTTP_STREAM_AND_EXECUTION',accounting,budget:JS_BUDGET,...result},null,2));
  expect(result.gzipBytes).toBeLessThanOrEqual(JS_BUDGET);
 }finally{await new Promise<void>(ok=>server.close(()=>ok()));}
});
