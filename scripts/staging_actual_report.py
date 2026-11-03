"""Summarize a real-file staging build: dbt result counts, row reconciliation, flag shares."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.local_duckdb import connect_local


def report(database: Path, run_results: Path, exit_code: int) -> dict:
    results = json.loads(run_results.read_text())["results"]
    statuses = {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    with connect_local(database) as conn:

        def rows(sql):
            return [list(r) for r in conn.execute(sql).fetchall()]

        raw = rows(
            "select 'lca', 'FY2024_Q4', count(*) from RAW_LCA.LCA_FY2024_Q4 union all "
            "select 'lca', 'FY2025_Q1', count(*) from RAW_LCA.LCA_FY2025_Q1 union all "
            "select 'lca', 'FY2025_Q2', count(*) from RAW_LCA.LCA_FY2025_Q2 union all "
            "select 'lca', 'FY2025_Q3', count(*) from RAW_LCA.LCA_FY2025_Q3 union all "
            "select 'lca', 'FY2025_Q4', count(*) from RAW_LCA.LCA_FY2025_Q4 union all "
            "select 'uscis', 'FY2022', count(*) from RAW_USCIS.USCIS_FY2022 union all "
            "select 'uscis', 'FY2023', count(*) from RAW_USCIS.USCIS_FY2023"
        )
        # Every LCA row is released; USCIS rows with a blank state, city and ZIP stay in
        # staging but are not released (user decision 2026-10-28), so they are counted here.
        # Released and excluded are counted separately, so a NULL flag breaks the sum.
        staged = {
            (s, r): (n, released, excluded)
            for s, r, n, released, excluded in rows(
                "select 'lca', source_release, count(*), count(*), 0 from STAGING.stg_lca "
                "group by all union all select 'uscis', source_release, count(*), "
                "count(*) filter (where released), count(*) filter (where not released) "
                "from STAGING.stg_uscis group by all"
            )
        }
        reconciliation = []
        for s, r, n in raw:
            staging_rows, released, excluded_rows = staged.get((s, r), (0, 0, 0))
            reconciliation.append(
                {
                    "source": s,
                    "release": r,
                    "raw_rows": n,
                    "staging_rows": staging_rows,
                    "released_rows": released,
                    "excluded_rows": excluded_rows,
                }
            )
        geography = rows(
            "select fiscal_year, geography_status, county_match_rule, count(*) "
            "from STAGING.stg_lca group by all order by all"
        )
        flag_shares = rows(
            "with f as ("
            " select 'lca' s, fiscal_year y, 'geo_unresolved' f, geo_unresolved v from STAGING.stg_lca"
            " union all select 'lca', fiscal_year, 'soc_unresolved', soc_unresolved from STAGING.stg_lca"
            " union all select 'uscis', fiscal_year, 'employer_name_missing', employer_name_missing"
            "  from STAGING.stg_uscis where released"
            " union all select 'uscis', fiscal_year, 'state_unresolved', state_unresolved"
            "  from STAGING.stg_uscis where released)"
            " select f.s, f.y, f.f, count(*), sum(v::int), round(sum(v::int) / count(*), 4),"
            " max(l.max_share)::double, sum(v::int) <= max(l.max_share) * count(*)"
            " from f join STAGING.staging_flag_limits l on l.source_system = f.s and l.flag = f.f"
            " group by all order by all"
        )
        # The state share the release gate sees, next to the share it would see if the
        # excluded rows were counted, so a pass cannot come from a changed limit unnoticed.
        uscis_release = rows(
            "select u.fiscal_year, count(*), sum((not released)::int),"
            " round(sum(state_unresolved::int) filter (where released)"
            "  / count(*) filter (where released), 4),"
            " round(sum(state_unresolved::int) / count(*), 4), max(l.max_share)::double"
            " from STAGING.stg_uscis u join STAGING.staging_flag_limits l"
            " on l.source_system = 'uscis' and l.flag = 'state_unresolved'"
            " group by all order by all"
        )
        unresolved_top = rows(
            "select fiscal_year, worksite_state, worksite_city, count(*) n from STAGING.stg_lca "
            "where geo_unresolved group by all order by n desc, 1, 2, 3 limit 25"
        )
    excluded = {}
    for r in reconciliation:
        if r["excluded_rows"]:
            excluded.setdefault(r["source"], {})[r["release"]] = r["excluded_rows"]
    return {
        "database": str(database),
        "exit_code": exit_code,
        "dbt_status_counts": statuses,
        "failed_or_errored": [
            {"unique_id": r["unique_id"], "status": r["status"], "failures": r["failures"]}
            for r in results
            if r["status"] in {"fail", "error"}
        ],
        "reconciliation": reconciliation,
        "rows_reconcile": all(
            r["raw_rows"] == r["staging_rows"] == r["released_rows"] + r["excluded_rows"]
            for r in reconciliation
        ),
        "excluded_rows": excluded,
        "flag_shares": [
            dict(
                zip(
                    ["source", "fiscal_year", "flag", "rows", "flagged", "share", "limit", "ok"],
                    f,
                )
            )
            for f in flag_shares
        ],
        "geography_by_year": [
            dict(zip(["fiscal_year", "geography_status", "county_match_rule", "rows"], g))
            for g in geography
        ],
        "uscis_release": [
            dict(
                zip(
                    [
                        "fiscal_year",
                        "rows",
                        "blank_address_excluded",
                        "state_unresolved_released_share",
                        "state_unresolved_share_if_included",
                        "state_unresolved_limit",
                    ],
                    u,
                )
            )
            for u in uscis_release
        ],
        "top_unresolved_cities": [
            dict(zip(["fiscal_year", "state", "city", "rows"], u)) for u in unresolved_top
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-results", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = report(args.database, args.run_results, args.exit_code)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    keys = ["exit_code", "dbt_status_counts", "rows_reconcile", "excluded_rows"]
    print(json.dumps({k: data[k] for k in keys}))


if __name__ == "__main__":
    main()
