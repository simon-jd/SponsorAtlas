"""Run real dbt SCD2 capture/change/repeat and reject destructive mutations locally."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from er.io import dump
from scripts.canonical_paths import DIMENSIONS_DB
from scripts.local_duckdb import connect_local

ROOT = Path(__file__).resolve().parents[1]
INDIRECT_SELECTION = "cautious"
# A standalone dimension database intentionally has no facts or FACTS_INPUT.
# Keep multi-parent dimension invariants explicit under cautious selection.
SELECTION = [
    "+dim_employer",
    "+dim_occupation",
    "+dim_geography",
    "+dim_date",
    "+int_dimension_mapping",
    "scd2_no_overlap",
    "dimension_reference_integrity",
    "dimension_mapping_accounting",
]


def verify(output, source_database):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / ".tools/dimensions"):
        raise ValueError("snapshot checks must stay inside .tools/dimensions")
    output.mkdir(parents=True, exist_ok=False)
    database = output / "snapshot-probe.duckdb"
    # A small necessary-column replay; never duplicate payloads or alter reviewed inputs.
    with connect_local(database) as c:
        c.execute(
            "attach '"
            + str(Path(source_database).resolve()).replace("'", "''")
            + "' as original (read_only)"
        )
        c.execute("create schema DIMENSIONS_INPUT")
        for table in ["employer_sources", "lca_attributes", "lineage"]:
            c.execute(
                f"create table DIMENSIONS_INPUT.{table} as select * from original.DIMENSIONS_INPUT.{table}"
            )
    env = dict(
        os.environ,
        SPONSORATLAS_DUCKDB_PATH=str(database),
        DBT_TARGET_PATH=str(output / "target"),
        DBT_LOG_PATH=str(output / "logs"),
        NO_COLOR="1",
    )
    evidence = {
        "dataset": "CONTROLLED_LOCAL_SNAPSHOT_REPLAY",
        "source_database": str(Path(source_database).resolve()),
        "runs": [],
    }

    with connect_local(database) as c:
        evidence["source_lineage"] = json.loads(
            c.execute("select manifest_json from DIMENSIONS_INPUT.lineage").fetchone()[0]
        )

    def dbt(label, args, *, expect_success=True):
        proc = subprocess.run(
            [
                str(ROOT / "dbt/run.sh"),
                "--warn-error",
                *args,
                "--indirect-selection",
                INDIRECT_SELECTION,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        (output / (label + ".log")).write_text(proc.stdout + proc.stderr)
        result_path = output / "target/run_results.json"
        results = (
            json.loads(result_path.read_text()).get("results", []) if result_path.exists() else []
        )
        result = {
            "label": label,
            "args": [*args, "--indirect-selection", INDIRECT_SELECTION],
            "exit_code": proc.returncode,
            "results": [
                {k: r.get(k) for k in ["unique_id", "status", "failures", "message"]}
                for r in results
            ],
        }
        evidence["runs"].append(result)
        dump(output / "evidence.json", evidence)
        if (proc.returncode == 0) != expect_success or (
            not expect_success and not any(r["status"] == "fail" for r in result["results"])
        ):
            raise AssertionError(
                f"{label}: unexpected dbt result; inspect {output / (label + '.log')}"
            )

    def versions():
        with connect_local(database) as c:
            return c.execute(
                "select employer_key,employer_id,valid_from,valid_to,is_current from MARTS.dim_employer order by employer_key"
            ).fetchall()

    dbt("01-initial", ["build", "--select", *SELECTION])
    initial = versions()
    with connect_local(database) as c:
        employer, source_key, old_name = c.execute(
            "select employer_id,attribute_source_key,employer_name from INTERMEDIATE.int_employer_current order by employer_id limit 1"
        ).fetchone()
        c.execute(
            "update DIMENSIONS_INPUT.employer_sources set source_name=source_name||' [CONTROLLED_LOCAL_PROBE]' where employer_id=? and source_employer_key=?",
            [employer, source_key],
        )
    dbt("02-source-change", ["run", "--select", "int_employer_current"])
    dbt("03-changed-snapshot", ["snapshot", "--select", "snap_employer"])
    dbt("04-changed-dimension", ["build", "--select", "dim_employer"])
    changed = versions()
    assert len(changed) == len(initial) + 1
    affected = sorted((r for r in changed if r[1] == employer), key=lambda r: r[2])
    assert (
        len(affected) == 2
        and affected[0][3] == affected[1][2]
        and not affected[0][4]
        and affected[1][4]
    )
    dbt("05-idempotent-snapshot", ["snapshot", "--select", "snap_employer"])
    dbt("06-idempotent-dimension", ["build", "--select", "dim_employer"])
    assert versions() == changed
    # Reorder all input rows: deterministic attribute selection must not create versions.
    with connect_local(database) as c:
        c.execute(
            "create or replace table DIMENSIONS_INPUT.employer_sources as select * from DIMENSIONS_INPUT.employer_sources order by unique_id desc"
        )
    dbt("07-reordered-source", ["run", "--select", "int_employer_current"])
    dbt("08-reordered-snapshot", ["snapshot", "--select", "snap_employer"])
    with connect_local(database) as c:
        assert c.execute("select count(*) from SNAPSHOTS.snap_employer").fetchone()[0] == len(
            changed
        )
        c.execute("create table MARTS.pristine_employer as select * from MARTS.dim_employer")
    mutations = {
        "overlap": "update MARTS.dim_employer set valid_to=(select max(valid_from)+interval '1 second' from MARTS.dim_employer where employer_id=?) where employer_id=? and not is_current",
        "double_current": "update MARTS.dim_employer set valid_to=NULL,is_current=true where employer_id=? and not is_current",
        "same_start": "update MARTS.dim_employer set valid_from=(select min(valid_from) from MARTS.dim_employer where employer_id=?) where employer_id=? and is_current",
        "reverse_interval": "update MARTS.dim_employer set valid_to=valid_from-interval '1 second' where employer_id=? and not is_current",
        "no_current": "delete from MARTS.dim_employer where employer_id=? and is_current",
    }
    for label, sql in mutations.items():
        with connect_local(database) as c:
            c.execute(sql, [employer] * sql.count("?"))
        dbt("reject-" + label, ["test", "--select", "scd2_no_overlap"], expect_success=False)
        with connect_local(database) as c:
            c.execute(
                "create or replace table MARTS.dim_employer as select * from MARTS.pristine_employer"
            )
    for label, sql, test in [
        (
            "duplicate_key",
            "insert into MARTS.dim_employer select * from MARTS.dim_employer limit 1",
            "unique_dim_employer_employer_key",
        ),
        (
            "null_key",
            "update MARTS.dim_employer set employer_key=NULL where employer_id=? and is_current",
            "not_null_dim_employer_employer_key",
        ),
    ]:
        with connect_local(database) as c:
            c.execute(sql, [employer] * sql.count("?"))
        dbt("reject-" + label, ["test", "--select", test], expect_success=False)
        with connect_local(database) as c:
            c.execute(
                "create or replace table MARTS.dim_employer as select * from MARTS.pristine_employer"
            )
    dbt(
        "09-restored-green",
        [
            "test",
            "--select",
            "dim_employer",
            "dimension_reference_integrity",
            "dimension_mapping_accounting",
        ],
    )
    with connect_local(database) as c:
        points = [affected[0][2], affected[0][3], affected[1][2]]
        for point in points:
            assert (
                c.execute(
                    "select count(*) from MARTS.dim_employer where employer_id=? and valid_from<=? and (valid_to is null or ?<valid_to)",
                    [employer, point, point],
                ).fetchone()[0]
                == 1
            )
    evidence.update(
        initial_versions=len(initial),
        changed_versions=len(changed),
        idempotent_versions=len(changed),
        probe_employer=employer,
        probe_original_name=old_name,
        probe_versions=[
            {
                "key": r[0],
                "valid_from_utc": r[2].isoformat(),
                "valid_to_utc": r[3].isoformat() if r[3] else None,
                "is_current": r[4],
            }
            for r in affected
        ],
        database_bytes=database.stat().st_size,
    )
    dump(output / "evidence.json", evidence)
    return evidence


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-database", type=Path, default=DIMENSIONS_DB)
    a = p.parse_args()
    result = verify(a.output, a.source_database)
    print(
        json.dumps(
            {
                "runs": len(result["runs"]),
                "initial_versions": result["initial_versions"],
                "changed_versions": result["changed_versions"],
                "rejections": sum(r["exit_code"] != 0 for r in result["runs"]),
            },
            indent=2,
        )
    )
