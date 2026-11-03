// Offline SDK seam only: actual reviewed lifecycle, no native connection/auth.
const test=require('node:test'),assert=require('node:assert/strict');
const {ReadOnlySnowflakeDriver,assertReadQuery}=require('../../semantic/source.cjs');
const {createReader,queryHashes}=require('../fabric/snowflake-subject.cjs');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const settings={cutoff:1000,config:{account:'offline-task16',readOnly:true}};
const snapshot='2025-01-01T00:00:00Z';
test('sealed template enum and original public policy reject arbitrary/private SQL',async()=>{
 const reader=createReader(settings,snapshot,()=>0);
 assert.deepEqual(Object.keys(reader).sort(),['preflight','read','release']);assert.ok(Object.isFrozen(reader));
 for(const name of ['SELECT * FROM ANALYTICS.RAW.secret','history; DROP TABLE x','FACT_MY_APPLICATIONS','__proto__',{},'history\0'])await assert.rejects(reader.read(name),/unknown/);
 for(const sql of ['SELECT * FROM ANALYTICS.SNAPSHOTS.snap_employer','SELECT * FROM ANALYTICS.DIMENSIONS_INPUT.lineage','SELECT * FROM ANALYTICS.MARTS.dim_employer'])assert.throws(()=>assertReadQuery(sql),/allowlist/);
 assert.doesNotThrow(()=>assertReadQuery('SELECT * FROM ANALYTICS.MARTS.fact_lca_case'));
 for(const value of ["2025-01-01T00:00:00Z'; DROP",'2025-02-30T00:00:00Z'])assert.throws(()=>queryHashes(value));
 await assert.rejects(reader.read('history','SELECT arbitrary'),/unknown/);
 await reader.release();
});
test('fixed history read keeps cancellation, dispatch cutoff, and awaited async release',async()=>{
 const original=ReadOnlySnowflakeDriver.prototype.getConnection;
 let now=0,resolveConnection,finishDestroy,destroyed=false,dispatches=0;
 const pending=new Promise(resolve=>{resolveConnection=resolve;});
 const connection={destroy(cb){finishDestroy=()=>{destroyed=true;cb(null);};},execute(){dispatches++;throw Error('must never dispatch');}};
 ReadOnlySnowflakeDriver.prototype.getConnection=function(){return pending;};
 const reader=createReader({...settings,cutoff:10},snapshot,()=>now);
 try{
  const request=reader.read('history');assert.equal(typeof request.cancel,'function');now=11;resolveConnection(connection);
  await assert.rejects(request,/cutoff|released/);
  let returned=false;const release=reader.release().then(()=>{returned=true;});await turn();assert.equal(returned,false);assert.equal(dispatches,0);assert.equal(destroyed,false);
  finishDestroy();await release;assert.equal(returned,true);assert.equal(destroyed,true);
 }finally{ReadOnlySnowflakeDriver.prototype.getConnection=original;}
});
test('fixed query is hydrated through reviewed driver and rejects native failures',async()=>{
 const original=ReadOnlySnowflakeDriver.prototype.getConnection;let statement,closed=false;
 const connection={destroy(cb){closed=true;queueMicrotask(()=>cb(null));},execute(options){statement=options.sqlText;const stmt={getColumns:()=>[],getQueryId:()=> 'offline',cancel:cb=>cb(null)};queueMicrotask(()=>options.complete(Error('offline denied relation'),stmt,[]));return stmt;}};
 ReadOnlySnowflakeDriver.prototype.getConnection=function(){return Promise.resolve(connection);};
 const reader=createReader(settings,snapshot,()=>0);
 try{await assert.rejects(reader.read('lineage'),/offline denied/);assert.match(statement,/ANALYTICS\.DIMENSIONS_INPUT\.lineage AT/);await reader.release();assert.equal(closed,true);}finally{ReadOnlySnowflakeDriver.prototype.getConnection=original;}
});

test('typed timestamp transport rejects sub-microsecond loss and schema mutation',()=>{
 const {schema,digest}=require('../fabric/content.cjs');
 assert.ok(Object.isFrozen(schema));assert.ok(Object.isFrozen(schema.history));
 const row=Object.fromEntries(schema.history.map(field=>[field.split(':')[0],null]));
 row.dbt_valid_from='2025-01-01T00:00:00.123456000';const expected=digest('history',[row]);
 row.dbt_valid_from='2025-01-01T00:00:00.123456';assert.deepEqual(digest('history',[row]),expected);
 row.dbt_valid_from='2025-01-01T00:00:00.123456001';assert.throws(()=>digest('history',[row]),/precision/);
});
