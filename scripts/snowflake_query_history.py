"""Per-query elapsed time and bytes scanned for the three service users, from
INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER (each user reads its own history; last 7 days,
no lag, no ACCOUNT_USAGE access needed). Query text is never selected: PUT statements
carry local paths. Partitions scanned and credits per query are not in this view (ACCOUNT_USAGE only).

    python -m scripts.snowflake_query_history --start 2026-10-31T00:00:00Z --output history.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from scripts.snowflake_live import SERVICE_USERS

COLUMNS = [
    "query_id", "query_type", "query_tag", "execution_status", "warehouse_name", "start_time",
    "total_elapsed_time", "execution_time", "bytes_scanned", "rows_produced",
]  # fmt: skip


# The table function lives in a database the role can use; LOADER has no ANALYTICS usage.
DATABASES = {"LOADER": "RAW", "TRANSFORMER": "ANALYTICS", "ANALYST": "ANALYTICS"}


def history_sql(database: str = "ANALYTICS") -> str:
    if database not in {"RAW", "ANALYTICS"}:
        raise ValueError("unexpected database")
    return (
        f"SELECT {', '.join(c.upper() for c in COLUMNS)} FROM TABLE("
        f"{database}.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(USER_NAME => %(user)s,"
        " END_TIME_RANGE_START => %(start)s::TIMESTAMP_LTZ, RESULT_LIMIT => 10000))"
        " ORDER BY START_TIME"
    )


def _int(value):
    return int(value or 0)


def summarize(user: str, rows: list[dict], *, top: int = 15) -> dict:
    # In-flight rows (e.g. this history query) report meaningless elapsed times.
    running = sum(1 for r in rows if r["execution_status"] == "RUNNING")
    rows = [r for r in rows if r["execution_status"] != "RUNNING"]
    groups = defaultdict(lambda: {"queries": 0, "elapsed_ms": 0, "execution_ms": 0,
                                  "bytes_scanned": 0, "rows_produced": 0})  # fmt: skip
    for r in rows:
        g = groups[(r["query_tag"] or "", r["query_type"])]
        g["queries"] += 1
        g["elapsed_ms"] += _int(r["total_elapsed_time"])
        g["execution_ms"] += _int(r["execution_time"])
        g["bytes_scanned"] += _int(r["bytes_scanned"])
        g["rows_produced"] += _int(r["rows_produced"])
    ranked = sorted(rows, key=lambda r: -_int(r["total_elapsed_time"]))[:top]
    return {
        "user": user,
        "queries": len(rows),
        "running_excluded": running,
        "warehouse_queries": sum(1 for r in rows if r["warehouse_name"]),
        "total_elapsed_ms": sum(_int(r["total_elapsed_time"]) for r in rows),
        "total_execution_ms": sum(_int(r["execution_time"]) for r in rows),
        "total_bytes_scanned": sum(_int(r["bytes_scanned"]) for r in rows),
        "failed": sum(1 for r in rows if r["execution_status"] != "SUCCESS"),
        "by_tag_and_type": [
            {"query_tag": tag, "query_type": kind, **g}
            for (tag, kind), g in sorted(groups.items(), key=lambda kv: -kv[1]["elapsed_ms"])
        ],
        "top_by_elapsed": [{c: (str(r[c]) if c == "start_time" else r[c]) for c in COLUMNS}
                           for r in ranked],  # fmt: skip
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="UTC, e.g. 2026-10-31T00:00:00Z")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    from scripts.snowflake_live import connect

    result = {"source": "INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER", "start": args.start, "users": []}
    for role, user in SERVICE_USERS.items():
        with connect(role, query_tag="sponsoratlas:phase8:history") as conn:
            cursor = conn.cursor()
            cursor.execute(history_sql(DATABASES[role]), {"user": user, "start": args.start})
            rows = [dict(zip(COLUMNS, row)) for row in cursor.fetchall()]
        result["users"].append(summarize(user, rows))
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, default=str)
        stream.write("\n")
    for u in result["users"]:
        print(f"{u['user']}\t{u['queries']} queries\t{u['total_elapsed_ms']} ms\t"
              f"{u['total_bytes_scanned']} bytes scanned\t{u['failed']} failed")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
