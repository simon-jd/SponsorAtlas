"""Native dbt selection boundaries for standalone dimensions, facts, and staging."""

import json
import os
import subprocess
from pathlib import Path

from scripts import dimension_checks

ROOT = Path(__file__).resolve().parents[2]


def selected_tests(tmp_path, selection, *, indirect=None):
    env = dict(
        os.environ,
        DBT_TARGET="local",
        SPONSORATLAS_DUCKDB_PATH=str(tmp_path / "unused.duckdb"),
        DBT_TARGET_PATH=str(tmp_path / "target"),
        DBT_LOG_PATH=str(tmp_path / "logs"),
    )
    command = [
        str(ROOT / "dbt/run.sh"),
        "--quiet",
        "--warn-error",
        "ls",
        "--select",
        *selection,
        "--resource-type",
        "test",
        "--output",
        "json",
    ]
    if indirect:
        command += ["--indirect-selection", indirect]
    proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return {
        json.loads(line)["unique_id"] for line in proc.stdout.splitlines() if line.startswith("{")
    }


def test_dimensions_keep_core_checks_without_fact_or_staging_dependencies(tmp_path):
    ids = selected_tests(
        tmp_path,
        dimension_checks.SELECTION,
        indirect=getattr(dimension_checks, "INDIRECT_SELECTION", "eager"),
    )
    assert {
        "test.sponsoratlas." + name
        for name in [
            "scd2_no_overlap",
            "dimension_reference_integrity",
            "dimension_mapping_accounting",
        ]
    } <= ids
    assert not any("fact_" in name or ".facts_" in name for name in ids), sorted(ids)
    assert not any(name.endswith(".staging_quality") for name in ids)


def test_facts_keep_all_relationships_grain_and_accounting_with_default_selection(tmp_path):
    ids = selected_tests(tmp_path, ["tag:facts"])
    assert len([name for name in ids if ".relationships_fact_" in name]) == 13
    assert {
        "test.sponsoratlas." + name
        for name in ["facts_grain", "facts_accounting", "facts_bls_headers", "facts_bls_conflicts"]
    } <= ids
    assert any(".unique_fact_lca_case_case_number." in name for name in ids)


def test_staging_default_keeps_original_quality_gates(tmp_path):
    ids = selected_tests(tmp_path, ["staging"])
    assert {
        "test.sponsoratlas." + name
        for name in [
            "staging_quality",
            "file_provenance",
            "mapping_integrity",
            "unmapped_columns",
            "board_provenance",
            "cdc_quality",
        ]
    } <= ids
