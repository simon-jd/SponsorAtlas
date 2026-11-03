// External binding is explicit and limited to the non-root container adapter.
function bindSettings(env=process.env){
 if(env.SEMANTIC_CONTAINER!==undefined&&env.SEMANTIC_CONTAINER!=='1')throw Error('Invalid container opt-in');
 const host=env.SEMANTIC_BIND_HOST||'127.0.0.1';
 if(host!=='127.0.0.1'&&!(host==='0.0.0.0'&&env.SEMANTIC_CONTAINER==='1'))throw Error('Invalid bind host');
 const raw=env.PORT||'4000';if(!/^\d{1,5}$/.test(raw)||Number(raw)<1024||Number(raw)>65535)throw Error('Invalid port');
 return {host,port:Number(raw)};
}
module.exports={bindSettings};
