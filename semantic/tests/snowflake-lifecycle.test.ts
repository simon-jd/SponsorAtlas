import test from 'node:test';
import {Readable} from 'node:stream';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {ReadOnlySnowflakeDriver}=require('../source.cjs');
const deferred=()=>{let resolve:any,reject:any;const promise=new Promise<any>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const make=(cutoff=1000,clock=()=>0)=>new ReadOnlySnowflakeDriver({cutoff,config:{account:'offline-lifecycle-only',readOnly:true}},clock);
function connection(){
 const calls:any={execute:[],destroy:0,cancel:0};
 const c:any={isUp:()=>true,connect(cb:any){cb(null,c);},execute(options:any){calls.execute.push(options.sqlText);const stmt={streamRows:()=>Readable.from([]),getColumns:()=>[],getQueryId:()=> 'offline',cancel(cb:any){calls.cancel++;options.complete(Error('cancelled'),stmt,[]);cb(null);}};if(!options.sqlText.includes('pending'))queueMicrotask(()=>options.complete(null,stmt,[]));return stmt;},destroy(cb:any){calls.destroy++;queueMicrotask(()=>cb(null));}};
 return {c,calls};
}
for(const method of ['query','memory','downloadQueryResults','stream'])test(`${method}: delayed connection cannot dispatch after cutoff`,async()=>{
 let now=0;const driver=make(10,()=>now);const pending=deferred();const {c,calls}=connection();driver.getConnection=()=>pending.promise;
 const query=driver[method]('SELECT 1',[],{});assert.equal(typeof query.cancel,'function');
 now=11;pending.resolve(c);
 await assert.rejects(query,/cutoff|released/i);await driver.release();assert.deepEqual(calls.execute,[]);assert.equal(calls.destroy,1);
});
test('deadline closes a still-connecting SDK connection and prevents late session statements',async()=>{
 const driver=make(Date.now()+25,Date.now);const {c,calls}=connection();let finish:any;
 c.connect=(cb:any)=>{finish=cb;};driver.createConnection=async()=>c;
 const query=driver.query('SELECT 1',[]);await assert.rejects(query,/cutoff|released/i);
 assert.ok(calls.destroy>0);finish(null,c);await turn();await driver.release();assert.deepEqual(calls.execute,[]);
});
test('cutoff is rechecked at SDK dispatch after the connection wait has resolved',async()=>{
 let now=0;const driver=make(10,()=>now);const {c,calls}=connection();driver.getConnection=()=>Promise.resolve(c);
 const wait=driver.waitFor.bind(driver);driver.waitFor=(promise:any)=>{const result=wait(promise);result.promise.then(()=>{now=11;});return result;};
 await assert.rejects(driver.query('SELECT 1',[]),/cutoff|released/i);await driver.release();assert.deepEqual(calls.execute,[]);assert.equal(calls.destroy,1);
});
test('reconnect crossing cutoff does not initialize a new session or dispatch the query',async()=>{
 let now=0;const driver=make(10,()=>now);const old=connection();old.c.isUp=()=>false;driver.connection=Promise.resolve(old.c);
 const next=connection();let finish:any;next.c.connect=(cb:any)=>{finish=cb;};driver.createConnection=async()=>next.c;
 const query=driver.query('SELECT 1',[]);await turn();now=11;finish(null,next.c);
 await assert.rejects(query,/cutoff|released/i);await driver.release();assert.deepEqual(next.calls.execute,[]);assert.equal(old.calls.destroy,1);assert.equal(next.calls.destroy,1);
});
test('cutoff interrupts pending session initialization and release awaits its destroy',async()=>{
 const driver=make(Date.now()+30,Date.now);const {c,calls}=connection();let destroyDone=false;
 c.execute=(options:any)=>{calls.execute.push(options.sqlText);return {cancel(cb:any){cb(null);}};};
 c.destroy=(cb:any)=>{calls.destroy++;setTimeout(()=>{destroyDone=true;cb(null);},10);};driver.createConnection=async()=>c;
 await assert.rejects(driver.query('SELECT 1',[]),/cutoff|released/i);await driver.release();assert.equal(destroyDone,true);assert.equal(calls.execute.length,1);assert.match(calls.execute[0],/^ALTER SESSION/);assert.equal(calls.destroy,1);
});
test('successful SDK initialization retains the fixed session controls',async()=>{
 const driver=make();const {c,calls}=connection();driver.createConnection=async()=>c;
 await driver.query('SELECT 1',[]);assert.equal(calls.execute.length,4);assert.match(calls.execute[0],/^ALTER SESSION/);assert.equal(calls.execute[1],'USE SECONDARY ROLES NONE');assert.match(calls.execute[2],/ABORT_DETACHED_QUERY=TRUE/);assert.equal(calls.execute[3],'SELECT 1');await driver.release();
});
test('cancellation remains on the original query promise before and after dispatch',async()=>{
 const driver=make();const pending=deferred();const a=connection();driver.getConnection=()=>pending.promise;
 const before=driver.query('SELECT 1',[]);await before.cancel();pending.resolve(a.c);await assert.rejects(before,/cancel/i);assert.deepEqual(a.calls.execute,[]);
 const b=connection();driver.getConnection=()=>Promise.resolve(b.c);const after=driver.query('SELECT pending',[]);await turn();await after.cancel();await assert.rejects(after,/cancel/i);await after.cancel();assert.equal(b.calls.cancel,1);await driver.release();
});
test('release waits for destroy callback and repeated calls share one cleanup',async()=>{
 const driver=make();const {c,calls}=connection();let finish:any;c.destroy=(cb:any)=>{calls.destroy++;finish=cb;};driver.connection=Promise.resolve(c);
 let returned=false;const first=driver.release();const second=driver.release();first.then(()=>{returned=true;});await turn();assert.equal(returned,false);assert.equal(calls.destroy,1);
 finish(null);await Promise.all([first,second]);assert.equal(returned,true);assert.equal(driver.connection,null);await driver.release();assert.equal(calls.destroy,1);await assert.rejects(driver.query('SELECT 1',[]),/released/i);
});
test('release waits for a pending connection, and tolerates a rejected connection',async()=>{
 const driver=make();const pending=deferred();driver.connection=pending.promise;const {c,calls}=connection();let returned=false;const released=driver.release().then(()=>{returned=true;});await turn();assert.equal(returned,false);pending.resolve(c);await released;assert.equal(calls.destroy,1);
 const rejected=make();rejected.connection=Promise.reject(Error('offline connect failure'));await rejected.release();await rejected.release();assert.equal(rejected.connection,null);
});
test('release rejects a destroy failure and never reports completed cleanup early',async()=>{
 const driver=make();const {c,calls}=connection();c.destroy=(cb:any)=>{calls.destroy++;queueMicrotask(()=>cb(Error('offline destroy failure')));};driver.connection=Promise.resolve(c);
 await assert.rejects(driver.release(),/destroy failure/);await assert.rejects(driver.release(),/destroy failure/);assert.equal(calls.destroy,1);
});
test('release retains a destroy failure from a rejected session initialization',async()=>{
 const driver=make();const {c}=connection();c.connect=(cb:any)=>cb(Error('offline connect failure'));c.destroy=(cb:any)=>cb(Error('offline destroy failure'));driver.createConnection=async()=>c;
 await assert.rejects(driver.query('SELECT 1',[]),/destroy failure/);await assert.rejects(driver.release(),/destroy failure/);
});
test('pinned Cube shutdown storage waits for the source destroy callback',async()=>{
 const {OrchestratorStorage}=require('@cubejs-backend/server-core/dist/src/core/OrchestratorStorage');
 const storage=new OrchestratorStorage();const driver=make();const {c}=connection();let finish:any;c.destroy=(cb:any)=>{finish=cb;};driver.connection=Promise.resolve(c);
 storage.set('offline-lifecycle-only',{release:()=>driver.release()});let returned=false;const stopped=storage.releaseConnections().then(()=>{returned=true;});await turn();assert.equal(returned,false);finish(null);await stopped;assert.equal(returned,true);
});
