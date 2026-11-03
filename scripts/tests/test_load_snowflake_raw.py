"""Offline tests for the Snowflake RAW loader: synthetic files and a fake connection."""

import hashlib
import json

import duckdb
import pytest

from scripts import load_snowflake_raw as loader


def envelope_file(tmp_path, release="FY2025_Q1", rows=3):
    path = tmp_path / f"LCA_{release}.jsonl"
    sha = hashlib.sha256(b"original").hexdigest()
    with path.open("w") as stream:
        for n in range(1, rows + 1):
            payload = json.dumps({"CASE_NUMBER": f"I-{n}", "NOTE": "é"})
            stream.write(json.dumps({"source_release": release, "source_sha256": sha,
                                     "source_url": "https://www.dol.gov/x.xlsx",
                                     "source_row_number": n, "payload_json": payload}) + "\n")  # fmt: skip
    original = tmp_path / f"LCA_{release}.xlsx"
    original.write_bytes(b"original")
    meta = {
        "table": f"RAW.LCA.LCA_{release}", "source": "LCA", "release": release,
        "sha256": sha, "source_rows": rows,
        "path": str(path), "original_path": str(original),
        "derived_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }  # fmt: skip
    (tmp_path / f"LCA_{release}.provenance.json").write_text(json.dumps(meta))
    return meta


def test_envelope_item_checks_hashes_and_counts_rows(tmp_path):
    meta = envelope_file(tmp_path)
    item = loader.envelope_item(meta)
    assert item["table"] == "RAW.LCA.LCA_FY2025_Q1" and item["expected_rows"] == 3
    assert item["file_sha256"] == meta["derived_sha256"]
    assert item["original_sha256"] == meta["sha256"]
    assert isinstance(item["local_fingerprint"], int)
    meta["derived_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="derived"):
        loader.envelope_item(meta)


def test_envelope_statements_put_to_table_stage_and_copy_typed_columns(tmp_path):
    item = loader.envelope_item(envelope_file(tmp_path))
    create, put, copy = loader.statements(item)
    assert create.startswith("CREATE TABLE RAW.LCA.LCA_FY2025_Q1 (SOURCE_RELEASE VARCHAR")
    assert "SOURCE_ROW_NUMBER NUMBER(38,0)" in create and "OR REPLACE" not in create
    assert put.startswith("PUT 'file://") and "@RAW.LCA.%LCA_FY2025_Q1" in put
    assert "AUTO_COMPRESS = TRUE" in put
    assert "FROM @RAW.LCA.%LCA_FY2025_Q1" in copy and "TYPE = JSON" in copy
    assert "$1:payload_json::VARCHAR" in copy and "ON_ERROR = ABORT_STATEMENT" in copy
    assert loader.statements(item, replace=True)[0].startswith("CREATE OR REPLACE TABLE")


def test_parquet_item_exports_and_maps_duckdb_types(tmp_path):
    db = tmp_path / "local.duckdb"
    with duckdb.connect(str(db)) as c:
        c.execute("create schema FACTS_INPUT")
        c.execute(
            "create table FACTS_INPUT.lca as select 'I-1' as case_number, 2025 as fiscal_year,"
            " cast(10.5 as decimal(18,2)) as wage_amount, date '2025-01-02' as decision_date,"
            " true as is_certified, 0.5::double as p, timestamp '2025-01-02 03:04:05' as t"
        )
    item = loader.parquet_item(
        db, "FACTS_INPUT", "lca", "RAW.LCA.FACTS_INPUT_LCA", tmp_path / "work",
        fingerprint=["case_number", "decision_date"],
    )  # fmt: skip
    files = sorted((tmp_path / "work/FACTS_INPUT_LCA").glob("*.parquet"))
    assert item["expected_rows"] == 1 and len(files) == 1
    assert item["file_sha256"] == {files[0].name: hashlib.sha256(files[0].read_bytes()).hexdigest()}
    assert item["path"].endswith("FACTS_INPUT_LCA/*.parquet")
    assert item["columns"] == [
        ("CASE_NUMBER", "VARCHAR"), ("FISCAL_YEAR", "NUMBER(38,0)"),
        ("WAGE_AMOUNT", "NUMBER(18,2)"), ("DECISION_DATE", "DATE"), ("IS_CERTIFIED", "BOOLEAN"),
        ("P", "FLOAT"), ("T", "TIMESTAMP_NTZ"),
    ]  # fmt: skip
    _, put, copy = loader.statements(item)
    assert "AUTO_COMPRESS = FALSE" in put
    assert "TYPE = PARQUET" in copy and "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in copy


def test_targets_and_identifiers_are_validated():
    with pytest.raises(ValueError):
        loader.check_table("RAW.LCA.X; DROP TABLE Y")
    with pytest.raises(ValueError):
        loader.check_table("ANALYTICS.MARTS.FACT_LCA_CASE")
    assert loader.check_table("RAW.JOBS.CDC_JOB_POSTINGS") == ("RAW", "JOBS", "CDC_JOB_POSTINGS")


class FakeCursor:
    def __init__(self, log, counts):
        self.log, self.counts, self.result, self.sfqid = log, counts, [], "q"

    def execute(self, sql, params=None):
        self.log.append(sql)
        if sql.startswith("SELECT COUNT(*)"):
            self.result = [self.counts.pop(0)]
        elif sql.startswith("COPY"):
            self.result = [("file", "LOADED", 3, 3)]
        else:
            self.result = [("ok",)]
        return self

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0]


class FakeConnection:
    def __init__(self, counts):
        self.log, self.counts = [], counts

    def cursor(self):
        return FakeCursor(self.log, self.counts)


def test_load_verifies_count_provenance_and_fingerprint(tmp_path):
    item = loader.envelope_item(envelope_file(tmp_path))
    good = FakeConnection([(3, 0, item["local_fingerprint"])])
    result = loader.load_item(good, item)
    assert result["status"] == "MATCH" and result["snowflake_rows"] == 3
    assert [s.split()[0] for s in good.log] == ["CREATE", "PUT", "COPY", "SELECT"]
    bad = FakeConnection([(2, 0, item["local_fingerprint"])])
    assert loader.load_item(bad, item)["status"] == "MISMATCH"
    tampered = FakeConnection([(3, 0, item["local_fingerprint"] + 1)])
    assert loader.load_item(tampered, item)["status"] == "MISMATCH"


def test_empty_table_creates_without_put_or_copy():
    item = loader.empty_envelope_item("RAW.BLS.BLS_FY2024", "FY2024")
    conn = FakeConnection([(0, None, None)])  # Snowflake COUNT_IF on no rows is NULL
    result = loader.load_item(conn, item)
    assert result["status"] == "MATCH"
    assert [s.split()[0] for s in conn.log] == ["CREATE", "SELECT"]
