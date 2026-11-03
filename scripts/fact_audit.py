"""Compare actual fact contents to retained source values and protected artifact hashes."""

import argparse
import json
from pathlib import Path

from scripts.build_bls_references import ROOT
from scripts.canonical_paths import FACTS_DB
from scripts.load_fact_inputs import protected_state
from scripts.local_duckdb import connect_local

OFFICIAL_WAGES = {
    "annual_mean_wage": "A_MEAN",
    "hourly_mean_wage": "H_MEAN",
    "annual_median_wage": "A_MEDIAN",
    "hourly_median_wage": "H_MEDIAN",
    **{
        f"{unit}_p{p}_wage": f"{prefix}_PCT{p}"
        for unit, prefix in [("annual", "A"), ("hourly", "H")]
        for p in [10, 25, 75, 90]
    },
}


def audit(database, output):
    database, output = Path(database).resolve(), Path(output).resolve()
    with connect_local(database) as c:
        result = {}
        for model, source in OFFICIAL_WAGES.items():
            result[model + "_mismatches"] = c.execute(
                f"select count(*) from MARTS.fact_wage_benchmark f left join RAW_BLS.BLS_FY2025 r on f.source_row_number=r.source_row_number and f.source_sha256=r.source_sha256 where r.source_row_number is null or f.{model} is distinct from try_cast(json_extract_string(r.payload_json,'$.{source}') as decimal(18,2))"
            ).fetchone()[0]
        queries = {
            "lca_winner_content_mismatches": "select count(*) from MARTS.fact_lca_case f left join FACTS_INPUT.lca l using(source_sha256,source_row_number) where l.source_row_number is null or f.case_status is distinct from l.case_status or f.annual_wage is distinct from l.annual_wage or f.decision_date is distinct from l.decision_date",
            "lca_newer_decision_exists": "select count(*) from MARTS.fact_lca_case f where exists(select 1 from FACTS_INPUT.lca l where l.case_number=f.case_number and l.decision_date>f.decision_date)",
            "job_observation_content_mismatches": "select count(*) from MARTS.fact_job_posting_daily f left join FACTS_INPUT.job_observations j on f.job_key=j.job_key and f.observed_at_utc=j.observed_at_utc where j.job_key is null or f.payload_json<>j.payload_json or f.source_sha256<>j.source_sha256 or f.observation_date<>cast(j.observed_at_utc as date)",
            "source_distinct_occupations": "select count(distinct soc_code) from STAGING.stg_bls",
            "fact_distinct_occupations": "select count(distinct soc_code) from MARTS.fact_wage_benchmark",
        }
        result.update({k: c.execute(sql).fetchone()[0] for k, sql in queries.items()})
        if any(v for k, v in result.items() if "mismatch" in k or "newer" in k):
            raise AssertionError("fact/source content mismatch")
        if result["source_distinct_occupations"] != result["fact_distinct_occupations"]:
            raise AssertionError("source occupation levels lost")
    baseline = json.loads((database.parent / "load-evidence.json").read_text())
    after = protected_state()
    if baseline["protection_before"] != after:
        raise AssertionError("protected source artifact changed")
    result.update(
        protected_files_unchanged=len(after), actual_database_bytes=database.stat().st_size
    )
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, default=FACTS_DB)
    p.add_argument("--output", type=Path, default=ROOT / ".tools/facts/content-audit.json")
    a = p.parse_args()
    print(json.dumps(audit(a.database, a.output), indent=2))
