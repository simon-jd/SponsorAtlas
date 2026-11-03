import {boundedJSON,validate} from '../../../lib/vitals/schema';
import {rumOrigin} from '../../../lib/vitals/origin';
import {configuredAdapter,store} from '../../../lib/vitals/store';
export const runtime='nodejs';
export const dynamic='force-dynamic';
let minute=0,count=0;
export async function POST(request:Request){
 const headers={'Cache-Control':'no-store'};
 const current=Math.floor(Date.now()/60000);if(current!==minute){minute=current;count=0;}
 if(++count>90)return new Response(null,{status:429,headers});
 // Same-origin browser transport, with no persisted header or address data.
 const origin=request.headers.get('origin');
 if(!origin)return new Response(null,{status:403,headers});
 let expected;
 try{expected=rumOrigin(process.env);}catch{return new Response(null,{status:503,headers});}
 if(origin!==expected)return new Response(null,{status:403,headers});
 if(!/^application\/json(?:;|$)/i.test(request.headers.get('content-type')||''))return new Response(null,{status:415,headers});
 let body;
 try{body=validate(await boundedJSON(request));}catch{return new Response(null,{status:400,headers});}
 try{await store(body,configuredAdapter(process.env));return new Response(null,{status:204,headers});}
 catch(e){const reason=(e as Error).message;return new Response(null,{status:['rate','quota'].includes(reason)?429:reason==='identity'?400:503,headers});}
}
