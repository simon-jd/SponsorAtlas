"""Native projection export: private fields never enter a published local snapshot."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_export_public_projection_and_reject_fanout():
    parent = ROOT / ".tools/orchestration"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="snapshot-test-", dir=parent) as directory:
        database = Path(directory) / "fixture.duckdb"
        create = """import duckdb,sys
from semantic.scripts.prepare import fixture
with duckdb.connect(sys.argv[1]) as c:
 fixture(c)
 c.execute('create schema MARTS')
 for table in ['dim_occupation','dim_geography','dim_date','fact_lca_case','fact_job_posting_daily','fact_wage_benchmark']:
  c.execute('create table MARTS.'+table+' as select * from ANALYTICS_TEST.'+table)
 c.execute("alter table MARTS.fact_job_posting_daily add column applicant_email varchar default 'PRIVATE@example.invalid'")
"""
        subprocess.run(
            [str(ROOT / ".venv/bin/python"), "-c", create, str(database)], cwd=ROOT, check=True
        )
        env = {**os.environ, "SPONSORATLAS_DUCKDB_PATH": str(database)}
        command = [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "orchestration.sponsoratlas_orchestration.snapshot",
            "test-export",
        ]
        first = subprocess.run(
            command, cwd=ROOT, env=env, check=True, capture_output=True, text=True
        )
        work = Path(first.stdout.strip())
        try:
            manifest = json.loads((work / "export-evidence.json").read_text())
            assert manifest["kind"] == "SYNTHETIC_ANALYTICS_TEST_ONLY"
            assert {Path(e["path"]).name for e in manifest["exports"]} == {
                "lca.parquet",
                "postings.parquet",
                "wages.parquet",
            }
            inspect = """import duckdb,sys
c=duckdb.connect()
for f in ['lca','postings','wages']:
 cols=[r[0] for r in c.execute("describe select * from read_parquet(?)",[sys.argv[1]+'/'+f+'.parquet']).fetchall()]
 assert 'applicant_email' not in cols,cols
"""
            subprocess.run([str(ROOT / ".venv/bin/python"), "-c", inspect, str(work)], check=True)
            corrupt = """import duckdb,sys
with duckdb.connect(sys.argv[1]) as c:
 c.execute('insert into MARTS.dim_date select * from MARTS.dim_date limit 1')
"""
            subprocess.run(
                [str(ROOT / ".venv/bin/python"), "-c", corrupt, str(database)], check=True
            )
            failed = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True, text=True, check=False
            )
            assert failed.returncode != 0, (
                "Export accepted a duplicate dimension and projection fanout"
            )
        finally:
            import shutil

            shutil.rmtree(work)
