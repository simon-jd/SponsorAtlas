"""Compare the Snowflake dbt build with the local DuckDB full-year build.

Per relation: row counts and one MD5 fingerprint per column (SUM of the first 32 bits of
MD5(column::varchar)); float and timestamp columns are skipped because the two engines
print them differently. The seven Cube metrics come from the same independent SQL on both
sides (semantic/scripts/prepare.py), read on Snowflake as CUBE_SVC / ANALYST; the
five-dimension LCA groups are hashed as in prepare.py. Relations are read as DBT_SVC /
TRANSFORMER. Local databases are opened read-only.

    python -m scripts.snowflake_compare --output compare.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import duckdb

from scripts.canonical_paths import DIMENSIONS_DB, FACTS_DB, STAGING_DB
from scripts.load_snowflake_raw import LIVE_REPLAY_KIND, fingerprint_sql

SKIP_TYPES = ("DOUBLE", "FLOAT", "REAL", "TIMESTAMP")
# Live board fetches load Snowflake only, so these cannot match the local replay once they exist.
JOB_POSTING_RELATIONS = {"fact_job_posting_daily"}
JOB_POSTING_METRICS = {"active_posting_days"}
LIVE_FACT_ROWS = (
    "SELECT COUNT(*) FROM ANALYTICS.MARTS.fact_job_posting_daily"
    f" WHERE REPLAY_KIND = '{LIVE_REPLAY_KIND}'"
)
# relation name -> (local database, local schema, Snowflake relation)
RELATIONS = {
    **{
        t: (STAGING_DB, "STAGING", f"ANALYTICS.STAGING.{t}")
        for t in ["stg_lca", "stg_uscis", "stg_board_jobs", "stg_cdc_job_postings",
                  "stg_cdc_application_events"]
    },
    # The local staging DB holds a 2-row BLS fixture; the fact build rebuilt stg_bls on real BLS.
    "stg_bls": (FACTS_DB, "STAGING", "ANALYTICS.STAGING.stg_bls"),
    "snap_employer": (FACTS_DB, "SNAPSHOTS", "ANALYTICS.INTERMEDIATE.snap_employer"),
    **{
        t: (FACTS_DB, "INTERMEDIATE", f"ANALYTICS.INTERMEDIATE.{t}")
        for t in ["int_dimension_mapping", "int_lca_case_candidates", "int_bls_benchmark_candidates"]
    },
    # Built by the dimension stage only; the fact database does not copy it.
    "int_employer_current": (DIMENSIONS_DB, "INTERMEDIATE", "ANALYTICS.INTERMEDIATE.int_employer_current"),
    **{
        t: (FACTS_DB, "MARTS", f"ANALYTICS.MARTS.{t}")
        for t in ["dim_date", "dim_employer", "dim_geography", "dim_occupation",
                  "fact_lca_case", "fact_job_posting_daily", "fact_wage_benchmark"]
    },
}  # fmt: skip


def metric_queries(s: str) -> dict:
    return {
        "lca": "select count(distinct case_number), sum(case when is_certified then 1 else 0 end),"
        " count(*), avg(case when is_certified then 1.0 else 0.0 end),"
        " percentile_cont(0.5) within group (order by annual_wage),"
        " percentile_cont(0.75) within group (order by annual_wage),"
        f" percentile_cont(0.9) within group (order by annual_wage) from {s}.fact_lca_case",
        "postings": f"select count(*) from {s}.fact_job_posting_daily where is_active",
        "wages": f"select count(*), max(annual_median_wage) from {s}.fact_wage_benchmark"
        " where soc_code='15-1252' and area_type='1' and area_code='99' and survey_year=2025",
    }


def group_query(s: str) -> str:
    return f"""select coalesce(f.employer_id,'UNKNOWN'),coalesce(o.major_code,'UNKNOWN'),
  coalesce(f.soc_code,'UNKNOWN'),coalesce(case when g.cbsa_type='Metropolitan Statistical Area' then g.cbsa_code end,'UNKNOWN'),
  f.fiscal_year,count(distinct f.case_number)
  from {s}.fact_lca_case f left join {s}.dim_occupation o on f.occupation_key=o.occupation_key
  left join {s}.dim_geography g on f.geography_key=g.geography_key group by 1,2,3,4,5"""


def _number(value):
    return None if value is None else float(value)


def metrics_from_rows(rows: dict) -> dict:
    cases, certified, latest, avg_rate, p50, p75, p90 = rows["lca"]
    return {
        "case_count": int(cases),
        "certified_cases": int(certified),
        "latest_rows": int(latest),
        "certified_rate": int(certified) / int(latest) if latest else None,
        "certified_rate_avg_sql": _number(avg_rate),
        "wage_p50": _number(p50),
        "wage_p75": _number(p75),
        "wage_p90": _number(p90),
        "active_posting_days": int(rows["postings"][0]),
        "oews_estimates_15_1252_us_2025": int(rows["wages"][0]),
        "oews_median_15_1252_us_2025": _number(rows["wages"][1]),
    }


def group_digest(rows) -> dict:
    canon = "\n".join(sorted("\x1f".join(str(x) for x in row) for row in rows))
    return {"count": len(rows), "sha256": hashlib.sha256(canon.encode()).hexdigest()}


def comparable(columns):
    used = [n for n, t in columns if not t.upper().startswith(SKIP_TYPES)]
    return used, [n for n, t in columns if t.upper().startswith(SKIP_TYPES)]


def fingerprint_query(relation: str, columns, *, snowflake: bool) -> str:
    parts = [fingerprint_sql([c], snowflake=snowflake) for c in columns]
    head = "SELECT COUNT(*)" if snowflake else "select count(*)"
    return f"{head}, {', '.join(parts)} {'FROM' if snowflake else 'from'} {relation}"


def local_table(c, schema: str, table: str) -> dict:
    columns = c.execute(
        "select column_name, data_type from information_schema.columns"
        " where table_schema = ? and table_name = ? order by ordinal_position",
        [schema, table],
    ).fetchall()
    used, skipped = comparable(columns)
    row = c.execute(fingerprint_query(f'"{schema}"."{table}"', used, snowflake=False)).fetchone()
    return {
        "rows": int(row[0]),
        "fingerprints": {n: (int(v) if v is not None else None) for n, v in zip(used, row[1:])},
        "skipped": skipped,
    }


def remote_table(cursor, relation: str, used) -> dict:
    row = cursor.execute(fingerprint_query(relation, used, snowflake=True)).fetchone()
    return {
        "rows": int(row[0]),
        "fingerprints": {n: (int(v) if v is not None else None) for n, v in zip(used, row[1:])},
    }


def diff(local: dict, remote: dict) -> dict:
    mismatched = [n for n, v in local["fingerprints"].items() if remote["fingerprints"].get(n) != v]
    same = local["rows"] == remote["rows"] and not mismatched
    return {
        "local_rows": local["rows"],
        "snowflake_rows": remote["rows"],
        "columns_compared": len(local["fingerprints"]),
        "mismatched_columns": mismatched,
        "skipped_columns": local.get("skipped", []),
        "status": "SAME" if same else "DIFF",
    }


def expected_divergence(name: str, status: str, live_rows: int) -> str:
    """A job-posting DIFF is expected once live rows exist; everything else keeps its status."""
    expected = status == "DIFF" and live_rows and name in JOB_POSTING_RELATIONS
    return "EXPECTED_DIVERGED" if expected else status


def metric_status(name: str, equal: bool, live_rows: int) -> str:
    if equal:
        return "EQUAL"
    return "EXPECTED_DIVERGED" if live_rows and name in JOB_POSTING_METRICS else "DIFF"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*", help="relation names (default: all)")
    args = parser.parse_args(argv)
    from scripts.snowflake_live import connect

    started, relations = time.monotonic(), {}
    local_dbs = {p: duckdb.connect(str(p), read_only=True) for p in {STAGING_DB, DIMENSIONS_DB, FACTS_DB}}
    with connect("TRANSFORMER", query_tag="sponsoratlas:phase8:compare") as dbt_conn:
        live_rows = int(dbt_conn.cursor().execute(LIVE_FACT_ROWS).fetchone()[0])
        for name, (database, schema, relation) in RELATIONS.items():
            if args.only and name not in args.only:
                continue
            local = local_table(local_dbs[database], schema, name)
            remote = remote_table(dbt_conn.cursor(), relation, list(local["fingerprints"]))
            relations[name] = {"snowflake_relation": relation, **diff(local, remote)}
            relations[name]["status"] = expected_divergence(
                name, relations[name]["status"], live_rows
            )
            print(f"{relations[name]['status']}\t{name}\t{local['rows']}\t{remote['rows']}"
                  f"\t{relations[name]['mismatched_columns']}")  # fmt: skip
    facts = local_dbs[FACTS_DB]
    local_metrics = metrics_from_rows(
        {k: facts.execute(q).fetchone() for k, q in metric_queries("MARTS").items()}
    )
    local_groups = group_digest(facts.execute(group_query("MARTS")).fetchall())
    with connect("ANALYST", query_tag="sponsoratlas:phase8:compare") as cube_conn:
        cursor = cube_conn.cursor()
        identity = cursor.execute("SELECT CURRENT_USER(), CURRENT_ROLE()").fetchone()
        remote_metrics = metrics_from_rows(
            {k: cursor.execute(q).fetchone() for k, q in metric_queries("ANALYTICS.MARTS").items()}
        )
        remote_groups = group_digest(cursor.execute(group_query("ANALYTICS.MARTS")).fetchall())
    for db in local_dbs.values():
        db.close()
    metrics = {
        k: {"local": local_metrics[k], "snowflake": remote_metrics[k],
            "equal": local_metrics[k] == remote_metrics[k],
            "status": metric_status(k, local_metrics[k] == remote_metrics[k], live_rows)}
        for k in local_metrics
    }  # fmt: skip
    result = {
        "metric_identity": list(identity),
        "live_job_posting_rows": live_rows,
        "relations": relations,
        "metrics": metrics,
        "lca_groups": {"local": local_groups, "snowflake": remote_groups,
                       "equal": local_groups == remote_groups},
        "wall_seconds": round(time.monotonic() - started, 1),
    }  # fmt: skip
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    for k, v in metrics.items():
        print(f"{v['status']}\t{k}\t{v['local']}\t{v['snowflake']}")
    print(f"groups\t{local_groups}\t{remote_groups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
