"""Explicit native dbt migration/merge checks on a tiny isolated source projection."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_native_state_migration_preserves_schema_gate_and_grain(tmp_path):
    import duckdb
    import yaml

    project = tmp_path / "project"
    (project / "models").mkdir(parents=True)
    (project / "macros").mkdir()
    for filename in ["fact_lca_case.sql", "fact_lca_case.yml"]:
        shutil.copyfile(ROOT / "dbt/models/marts" / filename, project / "models" / filename)
    (project / "dbt_project.yml").write_text(
        "name: cost_native\nversion: '1.0'\nconfig-version: 2\nprofile: sponsoratlas\n"
    )
    (project / "macros/schema.sql").write_text(
        "{% macro generate_schema_name(custom_schema_name, node) -%}"
        "{{ custom_schema_name | trim if custom_schema_name else target.schema }}"
        "{%- endmacro %}"
    )
    # Two explicit source occurrences, bounded projection from read-only actual history.
    database = tmp_path / "cost.duckdb"
    with duckdb.connect(str(database), config={"threads": 1, "memory_limit": "256MB"}) as c:
        c.execute("ATTACH ? AS actual (READ_ONLY)", [str(ROOT / ".tools/facts/fy2025-full.duckdb")])
        c.execute("CREATE SCHEMA fixture")
        c.execute("CREATE TABLE fixture.candidates AS SELECT * FROM "
                  "actual.INTERMEDIATE.int_lca_case_candidates WHERE winner_rank=1 LIMIT 2")
        for table, predicate in [
            ("dim_employer", "employer_id IN (SELECT employer_id FROM fixture.candidates)"),
            ("dim_date", "calendar_date IN (SELECT decision_date FROM fixture.candidates)"),
            ("dim_occupation", "occupation_key IN (SELECT occupation_key FROM fixture.candidates)"),
            ("dim_geography", "geography_key IN (SELECT geography_key FROM fixture.candidates)"),
        ]:
            c.execute(f"CREATE TABLE fixture.{table} AS SELECT * FROM actual.MARTS.{table} "
                      f"WHERE {predicate}")
        # Deliberately distinct event values ensure no employer-state fallback can pass.
        c.execute("UPDATE fixture.candidates SET worksite_state='ZZ'")
    for model, table in [("int_lca_case_candidates", "candidates"),
                         *[(t, t) for t in ["dim_employer", "dim_date", "dim_occupation",
                                            "dim_geography"]]]:
        (project / "models" / (model + ".sql")).write_text(
            "{{ config(materialized='view') }} select * from fixture." + table
        )
    env = {**os.environ, "DBT_TARGET": "local", "SPONSORATLAS_DUCKDB_PATH": str(database),
           "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DBT_LOG_PATH": str(tmp_path / "logs"),
           "DBT_TARGET_PATH": str(tmp_path / "target"), "NO_COLOR": "1"}
    outcomes = []

    def run(label, *args, error=None):
        command = [str(ROOT / ".venv/bin/dbt"), "--debug", *args,
                   "--project-dir", str(project), "--profiles-dir", str(ROOT / "dbt")]
        proc = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True,
                              timeout=90, check=False)
        log = proc.stdout + proc.stderr
        (tmp_path / (label + ".log")).write_text(log)
        outcomes.append({"label": label, "exit": proc.returncode})
        if error:
            assert proc.returncode != 0 and error.lower() in log.lower(), log[-3000:]
        else:
            assert proc.returncode == 0, log[-3000:]
        return log

    run("initial", "build")
    with duckdb.connect(str(database)) as c:
        baseline = c.execute("SELECT * EXCLUDE(worksite_state) FROM MARTS.fact_lca_case "
                             "ORDER BY case_number").fetchall()
        assert c.execute("SELECT DISTINCT worksite_state FROM MARTS.fact_lca_case").fetchall() == [("ZZ",)]
        c.execute("ALTER TABLE MARTS.fact_lca_case DROP COLUMN worksite_state")
    run("old-schema-rejected", "run", "--select", "fact_lca_case", error="schema")
    run("explicit-rebuild", "build", "--full-refresh", "--select", "fact_lca_case")
    with duckdb.connect(str(database)) as c:
        assert c.execute("SELECT * EXCLUDE(worksite_state) FROM MARTS.fact_lca_case "
                         "ORDER BY case_number").fetchall() == baseline
        c.execute("UPDATE fixture.candidates SET worksite_state=NULL WHERE case_number="
                  "(SELECT min(case_number) FROM fixture.candidates)")
    log = run("native-merge", "build", "--select", "fact_lca_case")
    assert "merge into" in log.lower()
    with duckdb.connect(str(database)) as c:
        assert c.execute("SELECT count(*),count(DISTINCT case_number),"
                         "count(*) FILTER(WHERE worksite_state IS NULL) "
                         "FROM MARTS.fact_lca_case").fetchone() == (2, 2, 1)
    contract = project / "models/fact_lca_case.yml"
    original = contract.read_text()
    spec = yaml.safe_load(original)
    next(col for col in spec["models"][0]["columns"] if col["name"] == "worksite_state")["data_type"] = "integer"
    contract.write_text(yaml.safe_dump(spec))
    run("contract-type-rejected", "run", "--select", "fact_lca_case", error="contract")
    contract.write_text(original)
    run("restored-gates", "build", "--select", "fact_lca_case")
    (tmp_path / "native-summary.json").write_text(json.dumps(outcomes, indent=2))
