"""Native dbt source freshness against an isolated database: no fixture can bypass the gate."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_native_24h_freshness_gate(tmp_path):
    database = tmp_path / "freshness.duckdb"
    prepare = """import duckdb,sys
with duckdb.connect(sys.argv[1]) as c:
 c.execute('create schema RAW_JOBS')
 for t in ['ACME_GH_GREENHOUSE','ACME_LV_LEVER','ACME_AB_ASHBY','CDC_JOB_POSTINGS']:
  c.execute('create table RAW_JOBS.'+t+" as select timezone('UTC',current_timestamp)-interval '25 hours' as _airbyte_extracted_at")
"""
    subprocess.run([str(ROOT / ".venv/bin/python"), "-c", prepare, str(database)], check=True)
    env = {
        **os.environ,
        "DBT_TARGET": "local",
        "SPONSORATLAS_DUCKDB_PATH": str(database),
        "DBT_TARGET_PATH": str(tmp_path / "target"),
        "DBT_LOG_PATH": str(tmp_path / "logs"),
    }
    command = [
        str(ROOT / ".venv/bin/dbt"),
        "source",
        "freshness",
        "--project-dir",
        str(ROOT / "dbt"),
        "--profiles-dir",
        str(ROOT / "dbt"),
        "--select",
        "source:jobs.*",
    ]
    stale = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert stale.returncode != 0, stale.stdout + stale.stderr
    results = json.loads((tmp_path / "target/sources.json").read_text())["results"]
    assert len(results) == 4
    assert all(
        r["status"] == "error" and r["max_loaded_at_time_ago_in_s"] >= 25 * 3600 for r in results
    )
    update = (
        prepare[0 : prepare.index("with duckdb")]
        + """with duckdb.connect(sys.argv[1]) as c:
 for t in ['ACME_GH_GREENHOUSE','ACME_LV_LEVER','ACME_AB_ASHBY','CDC_JOB_POSTINGS']:
  c.execute('update RAW_JOBS.'+t+" set _airbyte_extracted_at=timezone('UTC',current_timestamp)")
"""
    )
    subprocess.run([str(ROOT / ".venv/bin/python"), "-c", update, str(database)], check=True)
    fresh = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    assert all(
        r["status"] == "pass"
        for r in json.loads((tmp_path / "target/sources.json").read_text())["results"]
    )
