"""Offline tests for the Snowflake/DuckDB build comparison; no account access."""

import duckdb

from scripts import snowflake_compare as compare


def test_metric_sql_reads_marts_in_both_dialects():
    duck = compare.metric_queries("MARTS")
    snow = compare.metric_queries("ANALYTICS.MARTS")
    assert set(duck) == set(snow) == {"lca", "postings", "wages"}
    assert "from MARTS.fact_lca_case" in duck["lca"]
    assert "from ANALYTICS.MARTS.fact_lca_case" in snow["lca"]
    assert "percentile_cont(0.9) within group" in snow["lca"]


def test_metrics_from_rows_keep_exact_numerator_and_denominator():
    metrics = compare.metrics_from_rows(
        {"lca": (6, 3, 6, 0.5, 30, 40, 76), "postings": (4,), "wages": (1, 80)}
    )
    assert metrics["case_count"] == 6 and metrics["certified_rate"] == 0.5
    assert metrics["certified_rate_avg_sql"] == 0.5
    assert metrics["certified_cases"] == 3 and metrics["wage_p90"] == 76.0
    assert metrics["active_posting_days"] == 4 and metrics["oews_median_15_1252_us_2025"] == 80.0


def test_column_fingerprints_skip_float_and_timestamp_columns():
    columns = [("a", "VARCHAR"), ("b", "DOUBLE"), ("c", "TIMESTAMP"), ("d", "DECIMAL(24,2)")]
    used, skipped = compare.comparable(columns)
    assert used == ["a", "d"] and skipped == ["b", "c"]
    sql = compare.fingerprint_query("ANALYTICS.MARTS.X", used, snowflake=True)
    assert sql.startswith("SELECT COUNT(*), SUM(TO_NUMBER(UPPER(SUBSTR(MD5(COALESCE(A::VARCHAR")
    assert "FROM ANALYTICS.MARTS.X" in sql


def test_local_fingerprints_and_diff():
    with duckdb.connect(":memory:") as c:
        c.execute("create schema MARTS")
        c.execute("create table MARTS.t as select 'x' as a, 1.5::double as b, 2 as n")
        local = compare.local_table(c, "MARTS", "t")
    assert local["rows"] == 1 and local["skipped"] == ["b"]
    remote = {"rows": 1, "fingerprints": dict(local["fingerprints"])}
    assert compare.diff(local, remote)["mismatched_columns"] == []
    remote["fingerprints"]["n"] += 1
    result = compare.diff(local, remote)
    assert result["mismatched_columns"] == ["n"] and result["status"] == "DIFF"


def test_group_hash_is_order_independent():
    a = compare.group_digest([("E1", "15", "15-1252", "UNKNOWN", 2025, 3), ("E2", "11", "x", "1", 2024, 1)])
    b = compare.group_digest([("E2", "11", "x", "1", 2024, 1), ("E1", "15", "15-1252", "UNKNOWN", 2025, 3)])
    assert a == b and a["count"] == 2
