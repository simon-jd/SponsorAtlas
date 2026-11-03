// Intentionally no URLs, identifiers from source data, DOM attribution or user identifiers.
export const names = ['LCP', 'INP', 'CLS'] as const;
export type MetricName = typeof names[number];
export type Sample = {id:string; name:MetricName; value:number; sequence:number};
export type Envelope = {v:1; navigation:string; page:'search'|'employer'|'compare'|'map'; locale:'en'|'zh'; device:'narrow'|'wide'; navigationType:'document'|'bfcache'; metrics:Sample[]};
export function pageScope(path:string): Pick<Envelope,'page'|'locale'> | null {
  const m=/^\/(en|zh)(?:\/(map|compare|employers\/[^/]+))?\/?$/.exec(path);
  if(!m)return null;
  return {locale:m[1] as Envelope['locale'],page:m[2]?.startsWith('employers/')?'employer':m[2] as 'map'|'compare'||'search'};
}
const exact=(v:unknown, keys:string[]):v is Record<string,unknown> => !!v && typeof v==='object' && !Array.isArray(v) && Object.keys(v).sort().join()===keys.sort().join();
export function validate(value:unknown): Envelope {
  if(!exact(value,['v','navigation','page','locale','device','navigationType','metrics'])||value.v!==1||typeof value.navigation!=='string'||!/^[-\w]{16,64}$/.test(value.navigation)||typeof value.page!=='string'||!['search','employer','compare','map'].includes(value.page)||typeof value.locale!=='string'||!['en','zh'].includes(value.locale)||typeof value.device!=='string'||!['narrow','wide'].includes(value.device)||typeof value.navigationType!=='string'||!['document','bfcache'].includes(value.navigationType)||!Array.isArray(value.metrics)||value.metrics.length<1||value.metrics.length>3)throw Error('schema');
  const seen=new Set();
  for(const m of value.metrics){
    if(!exact(m,['id','name','value','sequence'])||typeof m.id!=='string'||!/^[-\w.]{1,100}$/.test(m.id)||!names.includes(m.name as MetricName)||seen.has(m.name)||typeof m.value!=='number'||!Number.isFinite(m.value)||m.value<0||m.value>(m.name==='CLS'?100:3600000)||!Number.isSafeInteger(m.sequence)||Number(m.sequence)<1||Number(m.sequence)>10000)throw Error('schema');
    seen.add(m.name);
  }
  return value as Envelope;
}
export async function boundedJSON(request:Request,limit=2048):Promise<unknown>{
  if(!request.body)throw Error('body');
  const reader=request.body.getReader();let bytes=0;const chunks:Uint8Array[]=[];
  try{while(true){const {value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>limit)throw Error('size');chunks.push(value);}}finally{await reader.cancel();}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
