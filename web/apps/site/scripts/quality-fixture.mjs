import {mkdir,readFile,writeFile,chmod} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
export async function prepareFixture(root){
 const dir=resolve(root,'.tools/viz/fixture');
 const columns=['employer','fiscal_year','employer_history_status','latitude','longitude','county_reference_vintage','coordinate_reference_vintage','coordinate_semantics','county_mapping_status','annual_wage','count'];
 const id='emp_'+'1'.repeat(64);
 const rows=[[id,2025,'UNCERTAIN',37.77,-122.42,'2025','2025','SYNTHETIC_TEST_ONLY','FIXTURE',120000,20],[id,2024,'UNKNOWN',40.71,-74,'2024','2025','SYNTHETIC_TEST_ONLY','FIXTURE',100000,12],[null,2025,'UNKNOWN',null,null,null,null,null,'UNKNOWN',null,5]];
 const raw=JSON.stringify({columns,rows});
 const manifest=JSON.stringify({kind:'CONTROLLED_COORDINATE_FIXTURE',sourceSnapshot:'quality-fixture-v1',sha256:createHash('sha256').update(raw).digest('hex'),bytes:Buffer.byteLength(raw)});
 await mkdir(dir,{recursive:true});
 for(const [name,data] of [['frequencies.json',raw],['manifest.json',manifest]]){const file=resolve(dir,name);try{if(await readFile(file,'utf8')!==data)throw Error('Refusing to overwrite existing fixture '+name);}catch(e){if(e.code!=='ENOENT')throw e;await writeFile(file,data,{mode:0o444,flag:'wx'});}await chmod(file,0o444);}
}
