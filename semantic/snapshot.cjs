// Deployment source and writable work namespaces are explicit and separate.
// Legacy local workflows default to SEMANTIC_WORK without changing their interface.
const fs=require('node:fs');
const path=require('node:path');
function snapshotDirectory(env=process.env){
 const work=env.SEMANTIC_WORK;
 if(!work)throw Error('SEMANTIC_WORK required');
 const configured=env.SEMANTIC_SNAPSHOT;
 if(configured&&!path.isAbsolute(configured))throw Error('SEMANTIC_SNAPSHOT must be absolute');
 const source=path.resolve(configured||work);
 if(env.SEMANTIC_CONTAINER==='1'&&env.SEMANTIC_SOURCE==='parquet'){
  if(!configured)throw Error('Container requires separate immutable SEMANTIC_SNAPSHOT');
  const writable=fs.realpathSync(work),immutable=fs.realpathSync(source);
  const contains=(parent,child)=>child===parent||child.startsWith(parent===path.parse(parent).root?parent:parent+path.sep);
  if(contains(writable,immutable)||contains(immutable,writable))throw Error('Source and work namespaces must be separate');
  if(immutable!==source)throw Error('Source namespace must not use symlinks');
  // W_OK tests actual effective access (including ACL/group permissions), not file mode alone.
  for(let dir=source;;dir=path.dirname(dir)){
   if(!fs.lstatSync(dir).isDirectory())throw Error('Source ancestor must be a directory');
   let writableByRuntime=true;try{fs.accessSync(dir,fs.constants.W_OK);}catch(e){if(!['EACCES','EROFS'].includes(e.code))throw e;writableByRuntime=false;}
   if(writableByRuntime)throw Error('Writable source namespace ancestor: '+dir);
   if(dir===path.dirname(dir))break;
  }
  for(const name of ['lca.parquet','postings.parquet','wages.parquet','export-evidence.json']){
   const stat=fs.lstatSync(path.join(source,name));
   if(!stat.isFile()||(stat.mode&0o222)||stat.uid===process.getuid())throw Error('Immutable source entry required: '+name);
  }
 }
 return source;
}
module.exports={snapshotDirectory};
