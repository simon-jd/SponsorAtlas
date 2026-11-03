"""One reviewed read-only public projection; no source hashing/copying or writes."""
import hashlib
import json
import threading
from pathlib import Path
import duckdb
from scripts.canonical_paths import FACTS_DB

ROOT=Path(__file__).resolve().parents[2]
SOURCE=FACTS_DB
WORK=ROOT/'.tools/viz/actual'
QUERY='''SELECT f.employer_id AS employer, f.fiscal_year, f.employer_history_status,
 g.county_internal_latitude AS latitude, g.county_internal_longitude AS longitude,
 g.county_reference_vintage, g.coordinate_reference_vintage, g.coordinate_semantics,
 f.county_mapping_status, f.annual_wage, count(*) AS count
FROM MARTS.fact_lca_case f LEFT JOIN MARTS.dim_geography g ON f.geography_key=g.geography_key
GROUP BY ALL'''
CHECKS=[
'SELECT count(*) AS rows, count(DISTINCT geography_key) AS unique_keys FROM MARTS.dim_geography',
'SELECT count(*) AS rows, count(DISTINCT case_number) AS unique_cases FROM MARTS.fact_lca_case',
'SELECT count(*) AS joined_rows FROM MARTS.fact_lca_case f LEFT JOIN MARTS.dim_geography g ON f.geography_key=g.geography_key']

def main():
 WORK.mkdir(parents=True,exist_ok=True)
 target=WORK/'frequencies.json'
 if target.exists():raise RuntimeError('Immutable projection already exists')
 paths=[SOURCE,*[ROOT/f'.tools/semantic/actual/{n}.parquet' for n in ('lca','postings','wages')]]
 def stats():return {str(p):{'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in paths}
 before=stats()
 con=duckdb.connect(str(SOURCE),read_only=True,config={'memory_limit':'512MB','threads':1,'max_temp_directory_size':'64MB','temp_directory':str(WORK/'export-tmp')})
 timer=threading.Timer(120,con.interrupt);timer.start()
 try:
  checks=[list(con.execute(q).fetchone()) for q in CHECKS]
  assert checks[0][0]==checks[0][1] and checks[1][0]==checks[1][1] and checks[2][0]==checks[1][0]
  cursor=con.execute(QUERY);columns=[d[0] for d in cursor.description];total=0;null_wages=0;unmapped=0;groups=0
  with target.open('x') as stream:
   stream.write('{"columns":'+json.dumps(columns)+',"rows":[')
   while rows:=cursor.fetchmany(2000):
    for raw in rows:
     row=list(raw);row[3]=None if row[3] is None else float(row[3]);row[4]=None if row[4] is None else float(row[4]);row[9]=None if row[9] is None else float(row[9])
     total+=row[10];null_wages+=row[10] if row[9] is None else 0;unmapped+=row[10] if row[3] is None or row[4] is None else 0
     if groups:stream.write(',')
     stream.write(json.dumps(row,separators=(',',':'),allow_nan=False));groups+=1
    if stream.tell()>45_000_000:raise RuntimeError('Projection byte cap')
   stream.write(']}')
  assert total==checks[1][0]
 finally:timer.cancel();con.close()
 after=stats();assert before==after
 public=json.loads((ROOT/'.tools/semantic/actual/export-evidence.json').read_text())
 snapshot=next(e['sha256'] for e in public['exports'] if e['path'].endswith('/lca.parquet'))
 manifest={'kind':'COUNTY_INTERNAL_POINT_NOT_WORKSITE','sourceSnapshot':snapshot,'sha256':hashlib.file_digest(target.open('rb'),'sha256').hexdigest(),'bytes':target.stat().st_size,'rows':total,'frequencyRows':groups,'nullWages':null_wages,'unmapped':unmapped,'query':QUERY,'checks':checks,'sourceProtection':{'before':before,'after':after,'unchanged':before==after},'coordinateCaveat':'County internal point; Census2020 code continuity to Census2023 does not establish identical boundaries or genuine worksite coordinates'}
 (WORK/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');target.chmod(0o444);(WORK/'manifest.json').chmod(0o444)
 print(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
