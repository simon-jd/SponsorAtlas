import {gzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import type {Page} from '@playwright/test';
export const JS_BUDGET=200000;
const digest=(body:Buffer)=>({decodedBytes:body.length,gzipBytes:gzipSync(body).length,sha256:createHash('sha256').update(body).digest('hex')});
export async function measureScripts(page:Page,url:string){
 const cdp=await page.context().newCDPSession(page);await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
 const external:Array<Record<string,unknown>&{gzipBytes:number}>=[],pending:Promise<void>[]=[];let ordinal=0;
 const onResponse=(response:import('@playwright/test').Response)=>{
  if(response.request().resourceType()!=='script')return;
  const occurrence=++ordinal;
  pending.push((async()=>{if(!response.ok())throw Error(`Script response ${response.status()}: ${response.url()}`);const body=await response.body();external.push({occurrence,url:response.url(),...digest(body)});})());
 };
 page.on('response',onResponse);
 try{
  const response=await page.goto(url);if(!response?.ok())throw Error('Document response failed');
  await page.waitForLoadState('networkidle');await page.waitForTimeout(1200);await Promise.all(pending);
  // Original completed response includes streamed hydration and scripts that remove themselves.
  // DOMParser is inert; measuring page.content() after hydration would lose original script bodies.
  const documentBody=await response.body();
  const bodies=await page.evaluate(html=>Array.from(new DOMParser().parseFromString(html,'text/html').querySelectorAll('script')).map((s,index)=>({index,src:s.hasAttribute('src'),type:(s.getAttribute('type')||'').trim().toLowerCase(),body:s.textContent||''})),documentBody.toString('utf8'));
  const jsType=/^(?:(?:text|application)\/(?:x-)?(?:java|ecma)script|text\/javascript1\.[0-5]|text\/jscript|text\/livescript)$/;
  const inline=[],excluded=[];
  for(const s of bodies){
   if(s.src)continue; // Inline children of src scripts are not executed; response accounted above.
   if(s.type===''||s.type==='module'||jsType.test(s.type))inline.push({index:s.index,type:s.type||'classic',...digest(Buffer.from(s.body,'utf8'))});
   else excluded.push({index:s.index,type:s.type,decodedBytes:Buffer.byteLength(s.body)});
  }
  external.sort((a,b)=>Number(a.occurrence)-Number(b.occurrence));
  const externalGzipBytes=external.reduce((n,x)=>n+x.gzipBytes,0),inlineGzipBytes=inline.reduce((n,x)=>n+x.gzipBytes,0);
  return {documentURL:response.url(),documentSHA256:createHash('sha256').update(documentBody).digest('hex'),external,inline,excluded,externalGzipBytes,inlineGzipBytes,gzipBytes:externalGzipBytes+inlineGzipBytes};
 }finally{page.off('response',onResponse);await cdp.detach();}
}
export const accounting='Each external script response occurrence (full URL+ordinal, no dedup) and each executable inline body in original completed streamed HTML gzip independently with Node defaults; sum lengths. Includes classic/module/hydration and conservative nomodule. Excludes non-JS MIME data blocks and ignored inline children of src scripts; no HTML markup bytes. Cold browser cache, networkidle+1200ms, no user enhancement.';
