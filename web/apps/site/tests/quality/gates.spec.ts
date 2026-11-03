import {test,expect} from '@playwright/test';
import {measureScripts,accounting,JS_BUDGET} from './budget.ts';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
test('budget: every direct route locale/theme counts all automatically loaded JS, gzip <=200000',async({browser})=>{
 test.setTimeout(120000);const result=[];
 for(const locale of ['en','zh'])for(const theme of ['light','dark']){
  const context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'reduce'});await context.addCookies([{name:'sa-theme',value:theme,url:'http://127.0.0.1:3100'}]);
  const lookup=await context.newPage();await lookup.goto(`http://127.0.0.1:3100/${locale}`);const id=await lookup.locator('[data-employer-link]').first().getAttribute('data-employer-link');expect(id).toBeTruthy();await lookup.close();
  for(const path of [`/${locale}`,`/${locale}/employers/${id}`,`/${locale}/compare?ids=${id}`,`/${locale}/map`]){
   const page=await context.newPage();const measured=await measureScripts(page,'http://127.0.0.1:3100'+path);
   await page.locator('h1').waitFor();if(path.endsWith('/map'))await expect(page.getByTestId('viz-coverage')).toBeVisible();
   expect(measured.external.length).toBeGreaterThan(0);expect(measured.inline.length).toBeGreaterThan(0);
   result.push({path,locale,theme,...measured});await page.close();
  }await context.close();
 }
 const evidenceDir=process.env.QUALITY_EVIDENCE_DIRECTORY||'evidence/quality-budget';
 await mkdir(evidenceDir,{recursive:true});await writeFile(resolve(evidenceDir,process.env.QUALITY_EVIDENCE_DIRECTORY?'budget.json':`${process.env.QUALITY_RECEIPT_PREFIX||process.env.QUALITY_MODE}.json`),JSON.stringify({scope:accounting,budget:JS_BUDGET,results:result},null,2));
 for(const row of result)expect(row.gzipBytes,`${row.path} ${row.theme}`).toBeLessThanOrEqual(JS_BUDGET);
});
test('smoke: actual routes expose source disclosures, true Cube rows and map aggregation',async({page})=>{
 await page.goto('/en');await expect(page.locator('[data-employer-link]').first()).toBeVisible();await expect(page.locator('.disclaimer')).toContainText('LCA');
 const id=await page.locator('[data-employer-link]').first().getAttribute('data-employer-link');await page.goto(`/zh/employers/${id}`);await expect(page.locator('table')).toBeVisible();await page.goto(`/en/compare?ids=${id}`);await expect(page.locator('table')).toContainText(id!);await page.goto('/en/map');await expect(page.getByTestId('viz-coverage')).toBeVisible();await expect(page.getByTestId('viz-coverage')).toContainText('238,891');
});
test('rum: same-origin endpoint bounded schema, persistence and browser callbacks',async({page,request})=>{
 const url='http://127.0.0.1:3100/api/vitals';const payload={v:1,navigation:'qa-navigation-123456789',page:'search',locale:'en',device:'wide',navigationType:'document',metrics:[{id:'qa-lcp',name:'LCP',value:2100,sequence:1}]};
 expect((await request.post(url,{data:payload})).status()).toBe(403);
 expect((await request.post(url,{headers:{Origin:'https://foreign.example'},data:payload})).status()).toBe(403);
 expect((await request.post(url,{headers:{Origin:'http://127.0.0.1:3100','Content-Type':'text/plain'},data:JSON.stringify(payload)})).status()).toBe(415);
 expect((await request.post(url,{headers:{Origin:'http://127.0.0.1:3100','Content-Type':'application/json'},data:'x'.repeat(2049)})).status()).toBe(400);
 expect((await request.post(url,{headers:{Origin:'http://127.0.0.1:3100'},data:{...payload,url:'PRIVATE'}})).status()).toBe(400);
 expect((await request.post(url,{headers:{Origin:'http://127.0.0.1:3100'},data:payload})).status()).toBe(204);
 const receipts:unknown[]=[];page.on('request',r=>{if(r.url().endsWith('/api/vitals'))receipts.push(r.postDataJSON());});
 await page.goto('/en');await page.locator('[data-theme-toggle]').click();await page.waitForTimeout(200);await page.goto('/zh');await page.waitForTimeout(200);expect(receipts.length).toBeGreaterThan(0);
 const evidenceDir=process.env.QUALITY_EVIDENCE_DIRECTORY||'evidence/quality-rum-local';
 await mkdir(evidenceDir,{recursive:true});await writeFile(resolve(evidenceDir,'browser-receipts.json'),JSON.stringify({scope:'LOCAL_QA_NOT_FIELD',receipts},null,2));
});

test('misconfiguration: absent durable adapter returns503 over real production HTTP',async({request})=>{
 const response=await request.post('http://127.0.0.1:3100/api/vitals',{headers:{Origin:'http://127.0.0.1:3100'},data:{v:1,navigation:'misconfigured-123456789',page:'search',locale:'en',device:'wide',navigationType:'document',metrics:[{id:'test-lcp',name:'LCP',value:1900,sequence:1}]}});expect(response.status()).toBe(503);expect(response.headers()['cache-control']).toBe('no-store');
});
