"""Transform the bounded public aggregate only; never open the protected source."""
import json
import hashlib
from pathlib import Path
import duckdb
root=Path(__file__).resolve().parents[2];work=root/'.tools/viz/actual';source=work/'aggregate.json';data=json.loads(source.read_text());target=work/'h3.parquet'
if target.exists():raise RuntimeError('Immutable optional artifact already exists')
assert len(data['cells'])<=10000
c=duckdb.connect(':memory:',config={'memory_limit':'128MB','threads':1,'max_temp_directory_size':'0B'})
c.execute('CREATE TABLE cells(h3 VARCHAR,count BIGINT,wage_p50 DOUBLE,resolution INTEGER)')
c.executemany('INSERT INTO cells VALUES (?,?,?,?)',[(r['h3'],r['count'],r['wage_p50'],data['resolution']) for r in data['cells']])
c.execute('COPY cells TO ? (FORMAT PARQUET)',[str(target)]);c.close()
assert target.stat().st_size<5000000
manifest={'kind':'COUNTY_INTERNAL_POINT_NOT_WORKSITE','scope':{'resolution':3,'year':None,'employer':None},'rows':len(data['cells']),'bytes':target.stat().st_size,'sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest(),'sourceSnapshot':data['sourceSnapshot'],'projection':data['projection'],'coverage':data['coverage']}
mp=work/'h3-manifest.json';mp.write_text(json.dumps(manifest,indent=2)+'\n');assert target.stat().st_size+mp.stat().st_size<5001000;target.chmod(0o444);mp.chmod(0o444);source.unlink();print(json.dumps(manifest,indent=2))
