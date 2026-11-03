// Explicit local QA aggregation. Production field reporting requires a separate
// real collection receipt/time window; this CLI cannot promote local samples.
import {readFile,stat} from 'node:fs/promises';
import {aggregate,MAX_BYTES,RETENTION_MS} from '../lib/vitals/store.ts';
const file=process.argv[2];if(!file)throw Error('Explicit local QA state file required');
if((await stat(file)).size>MAX_BYTES)throw Error('Storage bound');
const state=JSON.parse(await readFile(file,'utf8'));
const now=Date.now(),rows=state.rows.filter(r=>r.received>=now-RETENTION_MS);
console.log(JSON.stringify({scope:'LOCAL_QA_NOT_REPRESENTATIVE_PRODUCTION_FIELD',method:'nearest-rank sorted value[ceil(0.75*N)-1]; final navigation/name value after id+sequence dedup',windowUTC:{start:rows.length?new Date(Math.min(...rows.map(r=>r.received))).toISOString():null,end:rows.length?new Date(Math.max(...rows.map(r=>r.received))).toISOString():null},groups:aggregate(rows,now),missing:'Absent INP/LCP callbacks are unobserved, never imputed zero. Soft navigations suppressed; document route pinned. Each BFcache restore receives a new navigation epoch.'},null,2));
