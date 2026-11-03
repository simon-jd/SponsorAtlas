import {pageScope,type Envelope,type Sample} from './schema.ts';
export type InputMetric={name:string;id:string;value:number;navigationType:string;navigationId:number};
export function reporter(path:string,device:Envelope['device'],emit:(e:Envelope)=>void,uuid:()=>string){
 let scope=pageScope(path),epoch=0;
 const navigations=new Map<string,string>(),sequences=new Map<string,number>();
 const report=(m:InputMetric)=>{
  const key=`${epoch}:${m.navigationId}`;
  // Explicit document-only collection. SPA soft-navigation metrics are suppressed.
  if(epoch>=20||!scope||m.navigationType==='soft-navigation'||!['LCP','INP','CLS'].includes(m.name)||!Number.isFinite(m.value)||m.value<0)return;
  if(!navigations.has(key)){if(navigations.size>=20)return;navigations.set(key,uuid());}
  if(!sequences.has(m.id)&&sequences.size>=60)return;
  const sequence=(sequences.get(m.id)||0)+1;if(sequence>10000)return;sequences.set(m.id,sequence);
  emit({v:1,...scope,device,navigation:navigations.get(key)!,navigationType:m.navigationType==='back-forward-cache'?'bfcache':'document',metrics:[{name:m.name as Sample['name'],id:m.id,value:m.value,sequence}]});
 };
 // Browser navigationId may remain zero across BFcache restores. Own the epoch.
 return Object.assign(report,{restore(nextPath:string){epoch++;scope=pageScope(nextPath);}});
}
