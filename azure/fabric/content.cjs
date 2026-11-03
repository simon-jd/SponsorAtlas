const crypto=require('node:crypto');
const schema=require('./content-schema.json');
for(const fields of Object.values(schema))Object.freeze(fields);Object.freeze(schema);
const columns=table=>schema[table].map(f=>f.split(':')[0]);
function scalar(value,kind){
 if(value===null)return null;
 if(kind==='bool'){if(value===true||['1','true'].includes(String(value).toLowerCase()))return '1';if(value===false||['0','false'].includes(String(value).toLowerCase()))return '0';throw Error('nonboolean content');}
 if(typeof value!=='string')throw Error('native content must be returned losslessly as SQL strings');
 if(kind==='number'){
  if(!/^[+-]?\d+(?:\.\d+)?$/.test(value))throw Error('invalid numeric content');
  const negative=value.startsWith('-');let [whole,fraction='']=value.replace(/^[+-]/,'').split('.');
  whole=whole.replace(/^0+(?=\d)/,'');fraction=fraction.replace(/0+$/,'');
  if(whole==='0'&&!fraction)return '0';return (negative?'-':'')+whole+(fraction?'.'+fraction:'');
 }
 if(kind==='timestamp'){
  if(!/^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d{1,9})?$/.test(value))throw Error('invalid timestamp');
  const [whole,fraction='']=value.replace(' ','T').split('.');if(/[^0]/.test(fraction.slice(6)))throw Error('capture precision exceeds microseconds');return whole+'.'+fraction.slice(0,6).padEnd(6,'0');
 }
 if(kind==='date'&&!/^\d{4}-\d\d-\d\d$/.test(value))throw Error('invalid date');
 return value;
}
function canonicalRows(table,rows){
 const fields=schema[table],names=columns(table);
 return rows.map(row=>{if(!Array.isArray(row)){if(Object.keys(row).sort().join('\0')!==[...names].sort().join('\0'))throw Error('content schema mismatch');row=names.map(k=>row[k]);}if(row.length!==fields.length)throw Error('content width');return row.map((v,i)=>scalar(v,fields[i].split(':')[1]));}).sort((a,b)=>Buffer.compare(Buffer.from(JSON.stringify(a)),Buffer.from(JSON.stringify(b))));
}
const sha=data=>crypto.createHash('sha256').update(data).digest('hex');
function digest(table,rows){const canonical=canonicalRows(table,rows);return {rows:canonical.length,sha256:sha(JSON.stringify({columns:schema[table],rows:canonical}))};}
function historyBytes(rows){const names=columns('history');return Buffer.from(canonicalRows('history',rows).map(r=>JSON.stringify(Object.fromEntries(names.map((n,i)=>[n,r[i]])))+'\n').join(''));}
module.exports={schema,columns,canonicalRows,digest,historyBytes,sha};
