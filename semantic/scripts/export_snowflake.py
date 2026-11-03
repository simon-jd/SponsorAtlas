"""Opt-in, SELECT-only export of public Snowflake marts before the explicit trial cutoff.

No warehouse/schema/user is created or altered. This entrypoint is NOT remote evidence
until supplied real credentials and successfully executed against the reviewed marts.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from scripts.project_limits import DISK_SHARE_BYTES
from scripts.snowflake_live import secondary_roles_disabled

ROOT = Path(__file__).resolve().parents[2]
NODE = ROOT / ".tools/node-runtime/node-v24.22.1-linux-x64/bin/node"
PROJECTIONS = json.loads((ROOT / "semantic/projections.json").read_text())
ROW_CAP = 1_000_000
BYTE_CAP = 128 * 1024 * 1024
TABLES = {
    "lca": "fact_lca_case",
    "postings": "fact_job_posting_daily",
    "wages": "fact_wage_benchmark",
}


# Integer numerator and denominator, divided in Python: Snowflake returns
# AVG(CASE ... 1.0 ELSE 0.0 END) at NUMBER scale 6, which is not the exact rate.
LCA_ORACLE_SQL = (
    "SELECT COUNT(DISTINCT case_number),SUM(CASE WHEN is_certified THEN 1 ELSE 0 END),"
    "PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY annual_wage),"
    "PERCENTILE_CONT(0.75) WITHIN GROUP(ORDER BY annual_wage),"
    "PERCENTILE_CONT(0.9) WITHIN GROUP(ORDER BY annual_wage) FROM ANALYTICS.MARTS.fact_lca_case"
)


def lca_oracle(row):
    cases, certified, p50, p75, p90 = row
    cases, certified = int(cases), int(certified or 0)
    wage = [float(v) if v is not None else None for v in (p50, p75, p90)]
    return {
        "case_count": cases,
        "certified_count": certified,
        "certified_rate": certified / cases if cases else None,
        "wage_p50": wage[0],
        "wage_p75": wage[1],
        "wage_p90": wage[2],
    }


def settings():
    # Same fail-closed source/RSA/trial validation as the serving path. No key/passphrase is serialized.
    script = "const s=require('./semantic/source.cjs').sourceSettings();if(s.source!=='snowflake')throw Error('Snowflake source required');console.log(JSON.stringify({cutoff:s.cutoff,start:s.start,expires:s.expires}));"
    result = subprocess.run(
        [str(NODE), "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def fingerprint(path):
    stat = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}


def export(name):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", name):
        raise ValueError("Use a simple unique snapshot name")
    config = settings()
    destination = ROOT / ".tools/semantic" / f"snowflake-export-{name}"
    stage = destination.with_name("." + destination.name + "-pending")
    if destination.exists() or stage.exists():
        raise ValueError("Snapshot paths must be new; existing snapshots are never overwritten")
    conservative_bytes = 0
    for directory, _, files in os.walk(ROOT):
        for filename in files:
            path = Path(directory) / filename
            try:
                conservative_bytes += path.stat().st_size
            except FileNotFoundError:
                continue
    if conservative_bytes + 512 * 1024 * 1024 > DISK_SHARE_BYTES:
        raise RuntimeError("Insufficient project quota for bounded export/validation")
    stage.mkdir(parents=True, mode=0o700)
    import snowflake.connector
    from pyarrow import parquet

    connection = None
    try:
        connection = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user="CUBE_SVC",
            role="ANALYST",
            warehouse="SPONSORATLAS_XS",
            database="ANALYTICS",
            schema="MARTS",
            authenticator="SNOWFLAKE_JWT",
            private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"],
            private_key_file_pwd=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
            client_session_keep_alive=False,
            session_parameters={
                "STATEMENT_TIMEOUT_IN_SECONDS": 240,
                "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 60,
                "ABORT_DETACHED_QUERY": True,
                "QUERY_TAG": "SponsorAtlas_Task7_Export_ReadOnly",
            },
        )
        cursor = connection.cursor()

        def run(sql):
            if int(time.time() * 1000) >= config["cutoff"]:
                raise RuntimeError("Trial export cutoff reached")
            return cursor.execute(sql)

        # Session control only; no object or data writes and no secondary-role escalation.
        run("USE SECONDARY ROLES NONE")
        identity = run(
            "SELECT CURRENT_USER(),CURRENT_ROLE(),CURRENT_WAREHOUSE(),CURRENT_SECONDARY_ROLES()"
        ).fetchone()
        if tuple(identity[:3]) != ("CUBE_SVC", "ANALYST", "SPONSORATLAS_XS"):
            raise RuntimeError("Unexpected Snowflake session identity")
        if not secondary_roles_disabled(identity[3]):
            raise RuntimeError("Secondary roles must be disabled")
        show = run("SHOW WAREHOUSES LIKE 'SPONSORATLAS_XS'")
        warehouse = [
            dict(zip([d[0].lower() for d in show.description], row)) for row in show.fetchall()
        ]
        if (
            len(warehouse) != 1
            or warehouse[0]["size"] != "X-Small"
            or int(warehouse[0]["auto_suspend"]) != 60
            or int(warehouse[0]["min_cluster_count"]) != 1
            or int(warehouse[0]["max_cluster_count"]) != 1
        ):
            raise RuntimeError("Warehouse must be X-Small with auto_suspend=60")
        counts = {}
        keys = {
            "dim_occupation": "occupation_key",
            "dim_geography": "geography_key",
            "dim_date": "date_key",
            "fact_lca_case": "case_number",
            "fact_job_posting_daily": "posting_id||':'||CAST(observation_date AS VARCHAR)",
            "fact_wage_benchmark": "soc_code||':'||area_type||':'||area_code||':'||CAST(survey_year AS VARCHAR)",
        }
        for table, key in keys.items():
            count, unique = run(
                f"SELECT COUNT(*),COUNT(DISTINCT {key}) FROM ANALYTICS.MARTS.{table}"
            ).fetchone()
            if count != unique or count > ROW_CAP:
                raise RuntimeError("Marts duplicate/null key or bounded row cap exceeded")
            counts[table] = int(count)
        versions = {}
        exports = []
        for cube, template in PROJECTIONS.items():
            sql = template.replace("{s}", "ANALYTICS.MARTS")
            before = run(f"SELECT COUNT(*),HASH_AGG(*) FROM ({sql}) projected").fetchone()
            if int(before[0]) != counts[TABLES[cube]]:
                raise RuntimeError("Projection fanout/drop")
            versions[cube] = [str(v) for v in before]
            file = stage / f"{cube}.parquet"
            writer = None
            written = 0
            try:
                result = run(f"SELECT * FROM ({sql}) projected LIMIT {ROW_CAP + 1}")
                for table in result.fetch_arrow_batches():
                    if table.nbytes > BYTE_CAP:
                        raise RuntimeError("Arrow chunk exceeds memory budget")
                    table = table.rename_columns([c.lower() for c in table.column_names])
                    if writer is None:
                        writer = parquet.ParquetWriter(file, table.schema, compression="zstd")
                    for batch in table.to_batches(max_chunksize=10_000):
                        written += batch.num_rows
                        if written > ROW_CAP:
                            raise RuntimeError("Export row cap exceeded")
                        writer.write_batch(batch)
                    if sum(p.stat().st_size for p in stage.glob("*.parquet")) > BYTE_CAP:
                        raise RuntimeError("Export disk budget exceeded")
                if writer is None:
                    raise RuntimeError("Empty public mart is not an accepted production snapshot")
            finally:
                if writer is not None:
                    writer.close()
            if written != counts[TABLES[cube]]:
                raise RuntimeError("Incomplete export")
            if sum(p.stat().st_size for p in stage.glob("*.parquet")) > BYTE_CAP:
                raise RuntimeError("Final Parquet snapshot exceeds disk budget")
            file.chmod(0o444)
            exports.append(
                {
                    **fingerprint(file),
                    "source_table": f"ANALYTICS.MARTS.{TABLES[cube]}",
                    "rows": written,
                    "query": sql,
                }
            )
        # Recheck projection hashes after all reads; a changing live source is not a validated snapshot.
        for cube, template in PROJECTIONS.items():
            sql = template.replace("{s}", "ANALYTICS.MARTS")
            after = [
                str(v)
                for v in run(f"SELECT COUNT(*),HASH_AGG(*) FROM ({sql}) projected").fetchone()
            ]
            if after != versions[cube]:
                raise RuntimeError("Live marts changed during export; retry into a new snapshot")
        row = run(LCA_ORACLE_SQL).fetchone()
        posting_count = run(
            "SELECT COUNT(*) FROM ANALYTICS.MARTS.fact_job_posting_daily WHERE is_active"
        ).fetchone()[0]
        wage = run(
            "SELECT COUNT(*),MAX(annual_median_wage) FROM ANALYTICS.MARTS.fact_wage_benchmark WHERE soc_code='15-1252' AND area_type='1' AND area_code='99' AND survey_year=2025"
        ).fetchone()
        oracle = {
            "lca": lca_oracle(row),
            "postings": int(posting_count),
            "oews_median": float(wage[1]) if wage[0] == 1 and wage[1] is not None else None,
        }
        for cube, template in PROJECTIONS.items():
            after = [
                str(v)
                for v in run(
                    f"SELECT COUNT(*),HASH_AGG(*) FROM ({template.replace('{s}', 'ANALYTICS.MARTS')}) projected"
                ).fetchone()
            ]
            if after != versions[cube]:
                raise RuntimeError("Live marts changed during oracle reconciliation")
        evidence = {
            "kind": "ACTUAL_SNOWFLAKE_PUBLIC_MARTS",
            "exported_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "trial": config,
            "identity": list(identity),
            "warehouse": {"name": "SPONSORATLAS_XS", "size": "X-Small", "auto_suspend": 60},
            "connector_version": importlib.metadata.version("snowflake-connector-python"),
            "source_versions": versions,
            "exports": exports,
            "oracle": oracle,
        }
        (stage / "export-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        (stage / "oracle.json").write_text(json.dumps(oracle, indent=2) + "\n")
        result = subprocess.run(
            [
                str(NODE),
                "--max-old-space-size=512",
                "semantic/scripts/validate_snapshot.cjs",
                str(stage),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        validation = json.loads(result.stdout)
        if not validation["all_seven_match"]:
            raise RuntimeError("Native snapshot verification failed")
        evidence["validation"] = validation
        for item in evidence["exports"]:
            item["path"] = str(destination / Path(item["path"]).name)
        (stage / "export-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        stage.rename(destination)
        print(
            json.dumps(
                {
                    "kind": evidence["kind"],
                    "snapshot": str(destination),
                    "rows": counts,
                    "validation": validation,
                }
            )
        )
    finally:
        if connection is not None:
            connection.close()
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    try:
        export(parser.parse_args().name)
    except Exception as error:  # noqa: BLE001 -- redact connector options and key material
        # Credentials/key material and connector options never enter error logs.
        raise SystemExit(
            f"Snowflake export failed ({type(error).__name__}); no accepted snapshot published"
        ) from None
