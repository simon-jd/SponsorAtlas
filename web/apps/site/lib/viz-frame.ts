// Per-frame main-thread work: performance.measure durations of the pan/zoom update
// (viz-script) and of deck.gl's draw callback span (viz-render) for the same frame, summed in
// integer nanosecond units. Zero is valid (below the browser's timer resolution).
export function evaluateFrameWork(script:number[],render:number[],rate:1|4){
 if(!script.length||script.length!==render.length||![1,4].includes(rate))throw Error('Invalid frame work sample');
 const ns=(v:number)=>{if(!Number.isFinite(v)||v<0)throw Error('Invalid frame measure');return Math.round(v*1000000);};
 const rank=Math.ceil(.95*script.length)-1;
 const p95=(values:number[])=>[...values].sort((a,b)=>a-b)[rank]/1000000;
 const s=script.map(ns),r=render.map(ns),frames=s.map((v,i)=>v+r[i]);
 const thresholdNanoseconds=rate===1?16700000:33000000;
 const p95Nanoseconds=[...frames].sort((a,b)=>a-b)[rank];
 return {frames:frames.map(v=>v/1000000),p95:p95Nanoseconds/1000000,p95Nanoseconds,scriptP95:p95(s),renderP95:p95(r),thresholdNanoseconds,samples:frames.length,passed:p95Nanoseconds<=thresholdNanoseconds,definition:'performance.measure viz-script + viz-render per frame, integer ns, nearest rank ceil(.95*n)-1'};
}
// DOMHighResTimeStamp exposes double milliseconds, not nanosecond clock accuracy.
// Convert EACH interval to integer arithmetic units before nearest rank. A residue
// <=1e-10ms is accepted solely as floating representation noise (<0.0001ns).
// Larger subunit residues fail validation; the physical thresholds never change.
export function evaluateFrameIntervals(intervals:number[],rate:1|4){
 if(!intervals.length||![1,4].includes(rate))throw Error('Invalid frame sample');
 let maxRepresentationResidueMs=0;
 const units=intervals.map(value=>{
  if(!Number.isFinite(value)||value<=0)throw Error('Invalid frame interval');
  const ns=Math.round(value*1000000);
  if(!Number.isSafeInteger(ns)||ns<=0)throw Error('Unsafe frame units');
  const residue=Math.abs(value-ns/1000000);
  if(residue>1e-10)throw Error('Frame interval has ambiguous subunit precision');
  maxRepresentationResidueMs=Math.max(maxRepresentationResidueMs,residue);return ns;
 });
 const rank=Math.ceil(.95*intervals.length)-1;
 const rawP95=[...intervals].sort((a,b)=>a-b)[rank];
 const p95Nanoseconds=units.sort((a,b)=>a-b)[rank];
 const thresholdNanoseconds=rate===1?16700000:33000000;
 return {rawP95,p95Nanoseconds,thresholdNanoseconds,maxRepresentationResidueMs,samples:intervals.length,passed:p95Nanoseconds<=thresholdNanoseconds,definition:'Per-interval integer nanosecond arithmetic units, then nearest rank ceil(.95*n)-1; no nanosecond clock accuracy claim'};
}
