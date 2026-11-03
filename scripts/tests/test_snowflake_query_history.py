"""Offline tests for the per-user query history summary; fake cursor, no account access."""

from scripts import snowflake_query_history as history


def test_query_uses_the_table_function_and_never_selects_query_text():
    sql = history.history_sql()
    assert "INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(" in sql
    assert "QUERY_TEXT" not in sql.upper()
    assert "%(user)s" in sql and "%(start)s" in sql
    assert "RAW.INFORMATION_SCHEMA" in history.history_sql(history.DATABASES["LOADER"])


def rows():
    cols = history.COLUMNS
    data = [
        ["q1", "COPY", "sponsoratlas:phase8:raw-load", "SUCCESS", "SPONSORATLAS_XS", "2026-10-31T00:03:00Z", 8000, 7000, 100, 50],
        ["q2", "CREATE_TABLE_AS_SELECT", "sponsoratlas_task3", "SUCCESS", "SPONSORATLAS_XS", "2026-10-31T02:54:20Z", 13000, 12000, 500, 717449],
        ["q3", "SELECT", "sponsoratlas_task3", "SUCCESS", None, "2026-10-31T02:54:21Z", 20, 0, 0, 1],
        ["q4", "SELECT", "x", "RUNNING", "SPONSORATLAS_XS", "2026-10-31T03:00:00Z", -179341, 0, 0, 0],
    ]  # fmt: skip
    return [dict(zip(cols, r)) for r in data]


def test_summary_groups_by_tag_and_type_and_ranks_by_elapsed():
    result = history.summarize("DBT_SVC", rows(), top=2)
    assert result["running_excluded"] == 1
    assert result["queries"] == 3 and result["total_elapsed_ms"] == 21020
    assert result["total_bytes_scanned"] == 600
    assert result["warehouse_queries"] == 2
    assert [q["query_id"] for q in result["top_by_elapsed"]] == ["q2", "q1"]
    groups = {(g["query_tag"], g["query_type"]): g for g in result["by_tag_and_type"]}
    assert groups[("sponsoratlas_task3", "CREATE_TABLE_AS_SELECT")]["bytes_scanned"] == 500
    assert "query_text" not in result["top_by_elapsed"][0]
