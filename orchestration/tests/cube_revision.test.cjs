const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const os=require('node:os');
const {acquireWorkLock,readReadyState,validateRevision}=require('../../semantic/maintenance.cjs');

test('trusted revision and ownership are fail-closed, atomic and recoverable',()=>{
 const work=fs.mkdtempSync(path.join(os.tmpdir(),'sponsoratlas-revision-'));
 try{
  for(const value of ['invalid',"';DROP TABLE x",'a'.repeat(31),'A'.repeat(32)])assert.throws(()=>validateRevision(value));
  const first=acquireWorkLock(work,'refresh');
  assert.throws(()=>acquireWorkLock(work,'gateway'),/owned|locked/);
  assert.throws(()=>acquireWorkLock(work,'refresh'),/owned|locked/);
  assert.equal(readReadyState(work),null);
  first.commit('a'.repeat(32));first.release();
  assert.equal(readReadyState(work).revision,'a'.repeat(32));
  const failed=acquireWorkLock(work,'refresh');failed.release();
  assert.equal(readReadyState(work).revision,'a'.repeat(32),'failed build must retain prior ready revision');
  const gateway=acquireWorkLock(work,'gateway');assert.throws(()=>gateway.commit('b'.repeat(32)),/refresh/);gateway.release();
  const second=acquireWorkLock(work,'refresh');second.commit('b'.repeat(32));second.release();
  assert.equal(readReadyState(work).revision,'b'.repeat(32));
  const state=path.join(work,'refresh-state.json');fs.chmodSync(state,0o666);
  assert.throws(()=>readReadyState(work),/trusted|permission/);
 }finally{fs.rmSync(work,{recursive:true,force:true});}
});
