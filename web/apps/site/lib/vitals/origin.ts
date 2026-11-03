// Canonical origin is configured server-side; never trust forwarded Host values.
export function rumOrigin(env:Record<string,string|undefined>):string {
 const value=env.RUM_ORIGIN||'',url=new URL(value);
 const localQA=url.protocol==='http:'&&url.hostname==='127.0.0.1'&&env.RUM_QA_ONLY==='1';
 if(url.origin!==value||url.username||url.password||(url.protocol!=='https:'&&!localQA))throw Error('configuration');
 return url.origin;
}
