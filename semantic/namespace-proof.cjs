// Explicit local deployment verification only; not a service endpoint/startup mutation.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const source=require('./snapshot.cjs').snapshotDirectory(),work=process.env.SEMANTIC_WORK;
const names=['lca.parquet','postings.parquet','wages.parquet','export-evidence.json'];
const hashes=()=>Object.fromEntries(names.map(n=>[n,crypto.createHash('sha256').update(fs.readFileSync(path.join(source,n))).digest('hex')]));
const before=hashes(),checks=[];
function denied(name,fn){try{fn();throw Error('MUTATION SUCCEEDED: '+name);}catch(e){if(!['EACCES','EPERM','EROFS'].includes(e.code))throw e;checks.push({name,error:e.code});}}
for(const name of names){
 const file=path.join(source,name),replacement=path.join(work,'namespace-replacement');fs.writeFileSync(replacement,'controlled replacement');
 denied('unlink:'+name,()=>fs.unlinkSync(file));
 denied('rename-over:'+name,()=>fs.renameSync(replacement,file));
 denied('replacement:'+name,()=>fs.writeFileSync(file,'controlled replacement'));
 fs.unlinkSync(replacement);
}
denied('new-source-entry',()=>fs.writeFileSync(path.join(source,'replacement'),'controlled'));
for(let dir=source;dir!==path.dirname(dir);dir=path.dirname(dir))denied('rename-ancestor:'+dir,()=>fs.renameSync(dir,dir+'-replacement'));
denied('create-root-entry',()=>fs.mkdirSync('/namespace-replacement'));
const after=hashes();if(JSON.stringify(before)!==JSON.stringify(after))throw Error('Source bytes changed');
console.log(JSON.stringify({uid:process.getuid(),source,work,before,after,checks}));
