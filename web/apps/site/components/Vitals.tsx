'use client';
import {useEffect} from 'react';
import {onCLS,onINP,onLCP} from 'web-vitals';
import {reporter} from '../lib/vitals/client';
let registered=false;
export function Vitals(){
 useEffect(()=>{
  if(registered)return;registered=true;
  if(navigator.doNotTrack==='1'||(navigator as Navigator & {globalPrivacyControl?:boolean}).globalPrivacyControl)return;
  // Pin the initial document route. Client-side route changes never relabel it.
  const report=reporter(location.pathname,innerWidth<768?'narrow':'wide',envelope=>{
   const body=JSON.stringify(envelope);
   if(!navigator.sendBeacon('/api/vitals',new Blob([body],{type:'application/json'})))
    void fetch('/api/vitals',{method:'POST',headers:{'Content-Type':'application/json'},body,keepalive:true}).catch(()=>{});
  },()=>crypto.randomUUID());
  window.addEventListener('pageshow',event=>{if(event.persisted)report.restore(location.pathname);});
  const options={reportAllChanges:false,reportSoftNavs:false};
  onCLS(report,options);onINP(report,options);onLCP(report,options);
 },[]);
 return null;
}
