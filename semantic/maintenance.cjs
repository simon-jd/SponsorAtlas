// Trusted per-work maintenance state. Both gateway and refresher hold an exclusive lease.
const fs=require('node:fs');const path=require('node:path');const crypto=require('node:crypto');
function validateRevision(value){if(typeof value!=='string'||!/^[a-f0-9]{32}$/.test(value))throw Error('Invalid Cube refresh revision');return value;}
function trustedWork(value){
 if(!value)throw Error('Explicit Cube work directory required');const work=path.resolve(value);
 fs.mkdirSync(work,{recursive:true,mode:0o700});const s=fs.lstatSync(work);
 if(!s.isDirectory()||s.isSymbolicLink()||fs.realpathSync(work)!==work||s.uid!==process.getuid())throw Error('Cube work directory must be trusted and owner-writable only');
 // All participating services share this owner; tighten its dedicated work directory.
 fs.chmodSync(work,0o700);return work;
}
function readReadyState(value){
 if(!value)return null;const work=trustedWork(value);const file=path.join(work,'refresh-state.json');
 if(!fs.existsSync(file))return null;const s=fs.lstatSync(file);
 if(!s.isFile()||s.isSymbolicLink()||s.nlink!==1||(s.mode&0o077)||s.uid!==process.getuid())throw Error('Cube revision state permissions are not trusted');
 const state=JSON.parse(fs.readFileSync(file,'utf8'));
 if(state.version!==1||state.status!=='ready')throw Error('Invalid Cube revision state');validateRevision(state.revision);return state;
}
function acquireWorkLock(value,kind){
 if(!['gateway','refresh'].includes(kind))throw Error('Invalid Cube work owner');const work=trustedWork(value);
 const lock=path.join(work,'work-owner.json');const nonce=crypto.randomUUID();let descriptor;
 try{descriptor=fs.openSync(lock,'wx',0o600);}catch{throw Error('Cube work is owned or locked; reconcile the exact owner before retrying');}
 try{fs.writeFileSync(descriptor,JSON.stringify({kind,pid:process.pid,nonce}));fs.fsyncSync(descriptor);}finally{fs.closeSync(descriptor);}
 let released=false;let previous;
 const check=()=>{if(released||JSON.parse(fs.readFileSync(lock,'utf8')).nonce!==nonce)throw Error('Cube work ownership changed');};
 const release=()=>{if(released)return;check();fs.unlinkSync(lock);released=true;};
 try{previous=readReadyState(work)?.revision||null;}catch(error){release();throw error;}
 return {release,commit(revision){
  check();if(kind!=='refresh')throw Error('Only a refresh owner can promote a revision');validateRevision(revision);
  if((readReadyState(work)?.revision||null)!==previous)throw Error('Cube ready revision changed during maintenance');
  const file=path.join(work,'refresh-state.json'),temporary=path.join(work,'.refresh-'+nonce+'.pending');
  try{
   const fd=fs.openSync(temporary,'wx',0o600);
   try{fs.writeFileSync(fd,JSON.stringify({version:1,status:'ready',revision})+'\n');fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
   fs.renameSync(temporary,file);const dir=fs.openSync(work,'r');try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}
   previous=revision;
  }finally{if(fs.existsSync(temporary))fs.unlinkSync(temporary);}
 }};
}
module.exports={validateRevision,readReadyState,acquireWorkLock};
