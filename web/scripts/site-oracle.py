"""Small independent SQL oracle over the existing public Parquet, never facts DB."""
import hashlib
import json
from pathlib import Path
import duckdb
root=Path(__file__).resolve().parents[2]
source=root/'.tools/semantic/actual/lca.parquet'
manifest=json.loads((source.parent/'export-evidence.json').read_text())
entry=next(row for row in manifest['exports'] if row['path']==str(source))
assert not source.stat().st_mode & 0o222
assert hashlib.sha256(source.read_bytes()).hexdigest()==entry['sha256']
con=duckdb.connect(config={'memory_limit':'256MB','threads':'1'})
con.read_parquet(str(source)).create_view('source')
identity,count=con.execute("SELECT employer,count(*) n FROM source WHERE employer<>'UNKNOWN' GROUP BY employer ORDER BY n DESC,employer LIMIT 1").fetchone()
rows=con.execute('SELECT fiscal_year,employer_history_status,count(*),median(annual_wage) FROM source WHERE employer=? GROUP BY ALL ORDER BY fiscal_year DESC,employer_history_status',[identity]).fetchall()
unresolved=con.execute("SELECT count(*) FROM source WHERE employer='UNKNOWN'").fetchone()[0]
result={'source':'existing read-only ACTUAL_PUBLIC_MARTS LCA Parquet','sha256':entry['sha256'],'independentEngine':duckdb.__version__,'id':identity,'count':count,'unresolved':unresolved,'rows':[{'year':r[0],'status':r[1],'count':r[2],'wage':float(r[3]) if r[3] is not None else None} for r in rows]}
con.close()
Path('evidence/actual-reference.json').write_text(json.dumps(result,indent=2)+'\n')
print('Independent public Parquet reference recorded; protected facts DB not opened.')
