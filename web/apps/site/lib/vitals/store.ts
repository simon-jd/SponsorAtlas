import {readFile,writeFile,rename,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import type {Envelope,Sample} from './schema.ts';
export const MAX_BYTES=2000000, MAX_RECORDS=6000, RETENTION_MS=7*86400000;
export type Row=Omit<Envelope,'metrics'> & Sample & {received:number};
export type State={v:1;minute:number;requests:number;rows:Row[]};
export const empty=():State=>({v:1,minute:0,requests:0,rows:[]});
export function merge(state:State,envelope:Envelope,now:number):State {
 const minute=Math.floor(now/60000),requests=state.minute===minute?state.requests+1:1;
 if(requests>60)throw Error('rate');
 const rows=state.rows.filter(r=>r.received>=now-RETENTION_MS);
 const {metrics,...scope}=envelope;
 for(const metric of metrics){
  const i=rows.findIndex(r=>r.navigation===scope.navigation&&r.name===metric.name);
  if(i>=0){const old=rows[i];if(old.id!==metric.id||old.page!==scope.page||old.locale!==scope.locale||old.device!==scope.device||old.navigationType!==scope.navigationType)throw Error('identity');if(metric.sequence>old.sequence)rows[i]={...scope,...metric,received:now};}
  else rows.push({...scope,...metric,received:now});
 }
 if(rows.length>MAX_RECORDS)throw Error('quota');
 return {v:1,minute,requests,rows};
}
function encode(state:State){const body=JSON.stringify(state);if(Buffer.byteLength(body)>MAX_BYTES)throw Error('quota');return body;}
function decode(raw:string):State {if(Buffer.byteLength(raw)>MAX_BYTES)throw Error('quota');const s=JSON.parse(raw);if(s.v!==1||!Array.isArray(s.rows)||s.rows.length>MAX_RECORDS||!Number.isInteger(s.minute)||!Number.isInteger(s.requests))throw Error('corrupt');return s;}
export interface Adapter{read():Promise<{state:State;etag:string|null}>;write(state:State,etag:string|null):Promise<boolean>}
// One process and one file: only for explicit local QA, never production durability.
export function localAdapter(directory:string):Adapter{
 const file=resolve(directory,'vitals.json');
 return {async read(){try{return {state:decode(await readFile(file,'utf8')),etag:null};}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return {state:empty(),etag:null};throw e;}},async write(state){await mkdir(directory,{recursive:true,mode:0o700});await writeFile(file+'.tmp',encode(state),{mode:0o600});await rename(file+'.tmp',file);return true;}};
}
// A single private, bounded Azure Block Blob. The container must already exist.
// A read/create/write SAS is server-only; no list/delete/account credentials required.
export function blobAdapter(raw:string):Adapter{
 const url=new URL(raw);
 if(url.protocol!=='https:'||!/^[-a-z0-9]+\.blob\.core\.windows\.net$/.test(url.hostname)||url.pathname!=='/vitals/vitals.json'||!url.searchParams.get('sig')||url.username||url.password||url.hash)throw Error('configuration');
 const headers={'x-ms-version':'2023-11-03'};
 return {async read(){
  const r=await fetch(url,{headers,cache:'no-store',signal:AbortSignal.timeout(4000)});
  if(r.status===404)return {state:empty(),etag:null};
  if(!r.ok)throw Error('storage');
  if(Number(r.headers.get('content-length'))>MAX_BYTES)throw Error('quota');
  const reader=r.body!.getReader();let n=0;const chunks:Uint8Array[]=[];
  try{while(true){const {done,value}=await reader.read();if(done)break;n+=value.length;if(n>MAX_BYTES)throw Error('quota');chunks.push(value);}}finally{await reader.cancel();}
  const etag=r.headers.get('etag');if(!etag)throw Error('storage');
  return {state:decode(Buffer.concat(chunks).toString()),etag};
 },async write(state,etag){
  const r=await fetch(url,{method:'PUT',headers:{...headers,'Content-Type':'application/json','x-ms-blob-type':'BlockBlob',...(etag?{'If-Match':etag}:{'If-None-Match':'*'})},body:encode(state),signal:AbortSignal.timeout(4000)});
  if(r.status===412)return false;if(!r.ok)throw Error('storage');return true;
 }};
}
let queue:Promise<unknown>=Promise.resolve();let pending=0;
export function store(envelope:Envelope,adapter:Adapter,now=Date.now()):Promise<void>{
 if(pending>=8)return Promise.reject(Error('rate'));pending++;
 const work=queue.catch(()=>{}).then(async()=>{for(let attempt=0;attempt<3;attempt++){const {state,etag}=await adapter.read();if(await adapter.write(merge(state,envelope,now),etag))return;}throw Error('conflict');});
 queue=work;return work.finally(()=>{pending--;});
}
export function configuredAdapter(env:Record<string,string|undefined>):Adapter{
 if(env.RUM_STORAGE==='azure-blob'&&env.RUM_BLOB_SAS_URL)return blobAdapter(env.RUM_BLOB_SAS_URL);
 if(env.RUM_STORAGE==='local'&&env.RUM_QA_ONLY==='1'&&env.RUM_LOCAL_DIR)return localAdapter(env.RUM_LOCAL_DIR);
 throw Error('configuration');
}
export function aggregate(rows:Row[],now=Date.now()){
 const groups:Record<string,number[]>={};
 for(const r of rows.filter(r=>r.received>=now-RETENTION_MS)){const k=[r.page,r.locale,r.device,r.navigationType,r.name].join('/');(groups[k]??=[]).push(r.value);}
 return Object.fromEntries(Object.entries(groups).map(([key,values])=>{values.sort((a,b)=>a-b);return[key,{n:values.length,p75:values[Math.ceil(.75*values.length)-1]}];}));
}
