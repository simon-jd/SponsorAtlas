"""Bounded public projection from the independent synthetic dbt run database."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from orchestration.sponsoratlas_orchestration.runtime import ROOT, required


def export_local(run_id):
    # DuckDB is deliberately provided only by the existing modeling environment.
    if __name__ != "__main__":
        result = subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "orchestration.sponsoratlas_orchestration.snapshot",
                run_id,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return Path(result.stdout.strip())
    import duckdb

    source = Path(required("SPONSORATLAS_DUCKDB_PATH")).resolve()
    if not source.is_relative_to(ROOT / ".tools/orchestration") or not source.is_file():
        raise ValueError("Synthetic export requires the separate orchestration-owned dbt database")
    parent = ROOT / ".tools/orchestration/snapshots"
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(
        tempfile.mkdtemp(
            prefix="run-" + hashlib.sha256(run_id.encode()).hexdigest()[:8] + "-", dir=parent
        )
    )
    projections = json.loads((ROOT / "semantic/projections.json").read_text())
    try:
        exports = []
        with duckdb.connect(
            str(source),
            read_only=True,
            config={"memory_limit": "512MB", "threads": 1, "max_temp_directory_size": "256MB"},
        ) as connection:
            for table, key in [
                ("dim_occupation", "occupation_key"),
                ("dim_geography", "geography_key"),
                ("dim_date", "date_key"),
                ("fact_lca_case", "case_number"),
                (
                    "fact_job_posting_daily",
                    "posting_id || ':' || cast(observation_date as varchar)",
                ),
                (
                    "fact_wage_benchmark",
                    "soc_code || ':' || area_type || ':' || area_code || ':' || cast(survey_year as varchar)",
                ),
            ]:
                if not connection.execute(
                    f"select count(*)=count(distinct {key}) from MARTS.{table}"
                ).fetchone()[0]:
                    raise RuntimeError("Snapshot duplicate or null public grain")
            for name, sql in projections.items():
                query = sql.format(s="MARTS")
                rows = connection.execute("select count(*) from (" + query + ")").fetchone()[0]
                table = {
                    "lca": "fact_lca_case",
                    "postings": "fact_job_posting_daily",
                    "wages": "fact_wage_benchmark",
                }[name]
                if rows != connection.execute("select count(*) from MARTS." + table).fetchone()[0]:
                    raise RuntimeError("Public projection fanout or row loss")
                if rows > 1_000_000:
                    raise RuntimeError("Local snapshot exceeds row cap")
                file = work / (name + ".parquet")
                connection.execute(
                    "copy (" + query + ") to ? (format parquet, compression zstd)", [str(file)]
                )
                if sum(f.stat().st_size for f in work.glob("*.parquet")) > 128 * 1024 * 1024:
                    raise RuntimeError("Local snapshot exceeds 128 MiB cap")
                file.chmod(0o444)
                exports.append(
                    {
                        "path": str(file),
                        "rows": rows,
                        "size": file.stat().st_size,
                        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                    }
                )
        manifest = {
            "kind": "SYNTHETIC_ANALYTICS_TEST_ONLY",
            "source_database": str(source),
            "run_id": run_id,
            "exports": exports,
        }
        (work / "export-evidence.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return work
    except BaseException:
        shutil.rmtree(work)
        raise


if __name__ == "__main__":
    import sys

    print(export_local(sys.argv[1]))
