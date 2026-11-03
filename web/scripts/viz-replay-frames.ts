import {readFileSync,readdirSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {evaluateFrameIntervals} from '../apps/site/lib/viz-frame.ts';
const results=readdirSync('evidence').filter(name=>/^viz-frame-.*[14]x\.json$/.test(name)).sort().map(file=>{
 const bytes=readFileSync('evidence/'+file),data=JSON.parse(bytes.toString());if(data.intervals.length!==240||data.cells!==544||data.context!=='webgl2'||data.after-data.before<200)throw Error('Not a genuine full workload trace');
 return {file,sha256:createHash('sha256').update(bytes).digest('hex'),renderer:data.renderer,software:data.software,rate:data.rate,cells:data.cells,drawnUpdates:data.after-data.before,previousRawCommandPassed:data.p95<=data.threshold,...evaluateFrameIntervals(data.intervals,data.rate)};
});
writeFileSync('evidence/viz-numeric-replay.json',JSON.stringify({kind:'OFFLINE_NUMERIC_CORRECTION_OF_RETAINED_GENUINE_TRACES',ruling:'.superpowers/sdd/4-sponsoratlas-data-frontend-plan/task-14-measurement-ruling.md',results},null,2)+'\n');console.log(JSON.stringify(results,null,2));
