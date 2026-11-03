"""Synthetic fixture tests; never real warehouse acceptance."""

import json

from scripts.reconcile_counts import main, reconcile


def test_reconcile_flags_mismatch(tmp_path):
    source = tmp_path / "file.csv"
    source.write_text('name,notes\nAcme,"two\nlines"\nBeta,ok\n', encoding="utf-8")
    manifest = [
        {
            "path": str(source),
            "format": "csv",
            "table": "RAW.LCA.FY2024_Q4",
            "sha256": "a" * 64,
            "release": "FY2024_Q4",
        }
    ]
    rows = reconcile(manifest, lambda table, sha: 1)
    assert rows[0]["source_rows"] == 2
    assert rows[0]["raw_rows"] == 1
    assert rows[0]["difference"] == -1
    assert rows[0]["status"] == "MISMATCH"


def test_main_invalid_input_exits_two(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([{"table": "RAW.LCA.X;DROP", "path": "missing.csv"}]))
    assert main([str(p), "--offline-counts", str(p)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_counter_parameterizes_table_and_sha():
    from scripts.reconcile_counts import snowflake_counter

    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            calls.append((sql, params))

        def fetchone(self):
            return [3]

    class Connection:
        def cursor(self):
            return Cursor()

    count = snowflake_counter(Connection())
    assert count("RAW.LCA.LCA_FY2024_Q4", "a" * 64) == 3
    assert calls == [
        (
            "SELECT COUNT(*) FROM IDENTIFIER(%s) WHERE SOURCE_SHA256 = %s",
            ("RAW.LCA.LCA_FY2024_Q4", "a" * 64),
        )
    ]


def test_cli_exit_match_mismatch_and_invalid(tmp_path):
    import subprocess
    import sys

    source = tmp_path / "fixture.csv"
    source.write_text("A\nx\ny\n")
    entry = {
        "path": str(source),
        "format": "csv",
        "table": "RAW.LCA.FIXTURE",
        "sha256": "a" * 64,
        "release": "FY2024_Q4",
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([entry]))
    counts = tmp_path / "counts.json"
    for value, expected in [(2, 0), (1, 1), ("invalid", 2)]:
        counts.write_text(json.dumps({"RAW.LCA.FIXTURE:" + "a" * 64: value}))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.reconcile_counts",
                str(manifest),
                "--offline-counts",
                str(counts),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == expected, result.stderr
        assert "SYNTHETIC" in result.stderr
        if expected != 2:
            assert "source_rows,raw_rows,difference,status" in result.stdout


def test_reconcile_detects_corrupted_original_before_query(tmp_path):
    from pathlib import Path

    import pytest

    from scripts.archive_sources import archive_release

    source = tmp_path / "source.csv"
    source.write_text("A\nx\n")
    meta = archive_release(
        {
            "id": "lca_fy2024_q4",
            "source": "LCA",
            "release": "FY2024_Q4",
            "format": "csv",
            "url": "https://www.dol.gov/fixture.csv",
        },
        source,
        tmp_path / "archive",
    )
    Path(meta["original_path"]).write_text("A\ny\n")
    with pytest.raises(ValueError, match="original SHA256"):
        reconcile([meta], lambda *_: pytest.fail("Must not query corrupted archive"))


def test_duckdb_counter_maps_raw_identifier_to_local_schema(tmp_path):
    from scripts.local_duckdb import connect_local
    from scripts.reconcile_counts import duckdb_counter

    path = tmp_path / "raw.duckdb"
    with connect_local(path) as conn:
        conn.execute("create schema RAW_LCA")
        conn.execute("create table RAW_LCA.LCA_FY2024_Q4 (source_sha256 varchar)")
        conn.execute(
            "insert into RAW_LCA.LCA_FY2024_Q4 values (?), (?), (?)",
            ["a" * 64, "a" * 64, "b" * 64],
        )
    count = duckdb_counter(path)
    assert count("RAW.LCA.LCA_FY2024_Q4", "a" * 64) == 2
    assert count("RAW.LCA.LCA_FY2024_Q4", "c" * 64) == 0


def test_cdc_reconcile_compares_source_with_current_raw_rows():
    from scripts.reconcile_counts import reconcile_cdc

    rows = reconcile_cdc(
        {"job_postings": 3, "application_events": 2},
        {"job_postings": 3, "application_events": 1},
    )
    assert [(r["table"], r["status"], r["difference"]) for r in rows] == [
        ("RAW.JOBS.CDC_APPLICATION_EVENTS", "MISMATCH", -1),
        ("RAW.JOBS.CDC_JOB_POSTINGS", "MATCH", 0),
    ]
