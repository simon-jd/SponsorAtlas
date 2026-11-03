"""Read-only summary of a facts DB: row counts, LCA cases per fiscal year and release, and the
fact/source content checks of scripts.fact_audit. Writes one JSON file."""

import argparse
import json
import os
from pathlib import Path

import duckdb

from scripts.canonical_paths import FACTS_DB
from scripts.fact_audit import OFFICIAL_WAGES

QUERIES = {
    "fact_lca_case_rows": "select count(*) from MARTS.fact_lca_case",
    "fact_lca_distinct_cases": "select count(distinct case_number) from MARTS.fact_lca_case",
    "facts_input_lca_rows": "select count(*) from FACTS_INPUT.lca",
    "fact_job_posting_daily_rows": "select count(*) from MARTS.fact_job_posting_daily",
    "fact_wage_benchmark_rows": "select count(*) from MARTS.fact_wage_benchmark",
    "dim_employer_rows": "select count(*) from MARTS.dim_employer",
    "dim_geography_rows": "select count(*) from MARTS.dim_geography",
    "lca_winner_content_mismatches": "select count(*) from MARTS.fact_lca_case f left join FACTS_INPUT.lca l using(source_sha256,source_row_number) where l.source_row_number is null or f.case_status is distinct from l.case_status or f.annual_wage is distinct from l.annual_wage or f.decision_date is distinct from l.decision_date",
    "lca_newer_decision_exists": "select count(*) from MARTS.fact_lca_case f where exists(select 1 from FACTS_INPUT.lca l where l.case_number=f.case_number and l.decision_date>f.decision_date)",
}  # fmt: skip
GROUPS = {
    "lca_by_fiscal_year": "select fiscal_year, count(*) cases, sum(is_certified::int) certified, count(*) filter (where county_mapping_status <> 'MATCHED') county_not_matched from MARTS.fact_lca_case group by 1 order by 1",
    "lca_by_release": "select source_release, count(*) cases from MARTS.fact_lca_case group by 1 order by 1",
    "lca_county_mapping_status": "select county_mapping_status, count(*) cases from MARTS.fact_lca_case group by 1 order by 1",
}  # fmt: skip


def summarize(database):
    with duckdb.connect(str(database), read_only=True, config={"threads": 4}) as c:
        result = {"database": str(Path(database).resolve()), "bytes": Path(database).stat().st_size}
        result.update({k: c.execute(sql).fetchone()[0] for k, sql in QUERIES.items()})
        result["wage_mismatches"] = sum(
            c.execute(
                f"select count(*) from MARTS.fact_wage_benchmark f left join RAW_BLS.BLS_FY2025 r on f.source_row_number=r.source_row_number and f.source_sha256=r.source_sha256 where r.source_row_number is null or f.{model} is distinct from try_cast(json_extract_string(r.payload_json,'$.{source}') as decimal(18,2))"
            ).fetchone()[0]
            for model, source in OFFICIAL_WAGES.items()
        )  # fmt: skip
        for name, sql in GROUPS.items():
            cursor = c.execute(sql)
            columns = [d[0] for d in cursor.description]
            result[name] = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    result.update(nproc=os.cpu_count(), load=list(os.getloadavg()))
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, default=FACTS_DB)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    summary = summarize(a.database)
    a.output.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if not isinstance(v, list)}, indent=2))
