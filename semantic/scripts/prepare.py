"""Bounded read-only public-marts export and explicitly synthetic ANALYTICS_TEST data."""

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

from scripts.canonical_paths import FACTS_DB
from semantic.scripts.export_snowflake import lca_oracle

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = {"memory_limit": "512MB", "threads": 1, "max_temp_directory_size": "256MB"}
QUERIES = json.loads((ROOT / "semantic/projections.json").read_text())


def fingerprint(p):
    st = p.stat()
    with p.open("rb") as stream:
        h = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(p), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": h}


def fixture(c):
    c.execute("create schema ANALYTICS_TEST")
    c.execute("""create table ANALYTICS_TEST.dim_occupation as select * from (values
 ('O1','15-1252','15-0000'),('O2','17-2051','17-0000'),('OM','15-0000','15-0000'),('OS','29-9999','29-0000')) t(occupation_key,soc_code,major_code);
 create table ANALYTICS_TEST.dim_geography as select * from (values
 ('G1','M1','Metropolitan Statistical Area','MSA'),('GN',NULL,NULL,'NATIONAL')) t(geography_key,cbsa_code,cbsa_type,geography_level);
 create table ANALYTICS_TEST.dim_date as select * from (values (1,2025),(2,2026))t(date_key,fiscal_year);
 create table ANALYTICS_TEST.fact_lca_case as select * from (values
 ('C1',true,10.00,'A','O1','G1','15-1252',2025,'MATCHED_CAPTURE_INTERVAL'),
 ('C2',false,20.00,'A','O1','G1','15-1252',2025,'MATCHED_CAPTURE_INTERVAL'),
 ('C3',true,30.00,'B','O2','G1','17-2051',2025,'PRE_SNAPSHOT_HISTORY_UNKNOWN'),
 ('C4',true,40.00,'B','O2','G1','17-2051',2026,'PRE_SNAPSHOT_HISTORY_UNKNOWN'),
 ('C5',false,100.00,'B','O2',NULL,'17-2051',2026,'PRE_SNAPSHOT_HISTORY_UNKNOWN'),
 ('C6',false,NULL,NULL,NULL,NULL,NULL,2026,'UNRESOLVED_SOURCE_EMPLOYER'))
 t(case_number,is_certified,annual_wage,employer_id,occupation_key,geography_key,soc_code,fiscal_year,employer_history_status);
 create table ANALYTICS_TEST.fact_job_posting_daily as select * from (values
 ('J1',DATE '2025-05-01',true,'A','O1','G1',1),('J1',DATE '2025-05-02',true,'A','O1','G1',1),
 ('J2',DATE '2025-05-01',true,'B','O2','G1',1),('J3',DATE '2026-05-01',true,NULL,NULL,NULL,2),
 ('J4',DATE '2025-05-01',false,'A','O1','G1',1),('J5',DATE '2025-05-01',false,NULL,NULL,NULL,1))
 t(posting_id,observation_date,is_active,employer_id,occupation_key,geography_key,observation_date_key);
 alter table ANALYTICS_TEST.fact_job_posting_daily add column employer_mapping_status varchar default 'SYNTHETIC';
 alter table ANALYTICS_TEST.fact_job_posting_daily add column occupation_mapping_status varchar default 'SYNTHETIC';
 alter table ANALYTICS_TEST.fact_job_posting_daily add column geography_mapping_status varchar default 'SYNTHETIC';
 create table ANALYTICS_TEST.fact_wage_benchmark as select * from (values
 ('15-1252','1','99',2025,'O1','GN','detailed',80.00,'80','REPORTED'),
 ('15-1252','4','M1',2025,'O1','G1','detailed',100.00,'100','REPORTED'),
 ('15-1252','1','99',2024,'O1','GN','detailed',60.00,'60','REPORTED'),
 ('15-0000','1','99',2025,'OM','GN','major',70.00,'70','REPORTED'),
 ('29-9999','1','99',2025,'OS','GN','detailed',NULL,'*','SUPPRESSED_OR_TOP_CODED'))
 t(soc_code,area_type,area_code,survey_year,occupation_key,geography_key,occupation_group,annual_median_wage,annual_median_wage_raw,wage_status);
 alter table ANALYTICS_TEST.fact_wage_benchmark add column annual_flag varchar default NULL;
 alter table ANALYTICS_TEST.fact_wage_benchmark add column hourly_flag varchar default NULL;
 """)


def prepare(kind):
    work = ROOT / ".tools/semantic" / kind
    work.mkdir(parents=True, exist_ok=True)
    source = FACTS_DB
    before = fingerprint(source) if kind == "actual" else None
    c = duckdb.connect(
        str(source) if kind == "actual" else ":memory:",
        read_only=kind == "actual",
        config={**SETTINGS, "temp_directory": str(work / "export-tmp")},
    )
    try:
        s = "MARTS" if kind == "actual" else "ANALYTICS_TEST"
        if kind == "fixture":
            fixture(c)
        # Dimension uniqueness is required for these many-to-one joins. Never join employer current attributes.
        for table, key in [
            ("dim_occupation", "occupation_key"),
            ("dim_geography", "geography_key"),
            ("dim_date", "date_key"),
        ]:
            assert c.execute(f"select count(*)=count(distinct {key}) from {s}.{table}").fetchone()[
                0
            ]
        exports = []
        for name, q in QUERIES.items():
            p = work / f"{name}.parquet"
            if p.exists():
                p.chmod(0o644)
                p.unlink()
            query = q.format(s=s)
            grain = {
                "lca": "case_number",
                "postings": "posting_id || ':' || cast(observation_date as varchar)",
                "wages": "soc_code || ':' || area_type || ':' || area_code || ':' || cast(survey_year as varchar)",
            }[name]
            table = {
                "lca": "fact_lca_case",
                "postings": "fact_job_posting_daily",
                "wages": "fact_wage_benchmark",
            }[name]
            assert c.execute(
                f"select count(*)=count(distinct {grain}) from {s}.{table}"
            ).fetchone()[0], "duplicate or null fact grain"
            count = c.execute(f"select count(*) from ({query})").fetchone()[0]
            table = {
                "lca": "fact_lca_case",
                "postings": "fact_job_posting_daily",
                "wages": "fact_wage_benchmark",
            }[name]
            assert count == c.execute(f"select count(*) from {s}.{table}").fetchone()[0], (
                "join fanout/drop"
            )
            c.execute(f"copy ({query}) to '{p}' (format parquet, compression zstd)")
            p.chmod(0o444)
            exports.append(
                {**fingerprint(p), "source_table": f"{s}.{table}", "rows": count, "query": query}
            )
        # Independent grouped reconciliation against the original fact/dimension joins.
        groups = c.execute(f"""select coalesce(f.employer_id,'UNKNOWN'),coalesce(o.major_code,'UNKNOWN'),
  coalesce(f.soc_code,'UNKNOWN'),coalesce(case when g.cbsa_type='Metropolitan Statistical Area' then g.cbsa_code end,'UNKNOWN'),
  f.fiscal_year,count(distinct f.case_number)
  from {s}.fact_lca_case f left join {s}.dim_occupation o on f.occupation_key=o.occupation_key
  left join {s}.dim_geography g on f.geography_key=g.geography_key group by 1,2,3,4,5""").fetchall()
        canon = "\n".join(sorted("\x1f".join(str(x) for x in row) for row in groups))
        group_oracle = {"count": len(groups), "sha256": hashlib.sha256(canon.encode()).hexdigest()}
        # Independent SQL oracle reads facts, not Cube SQL or the exported projection.
        row = c.execute(f"""select count(distinct case_number), sum(case when is_certified then 1 else 0 end),
  percentile_cont(0.5) within group(order by annual_wage),
  percentile_cont(0.75) within group(order by annual_wage),
  percentile_cont(0.9) within group(order by annual_wage) from {s}.fact_lca_case""").fetchone()
        wage = c.execute(
            f"select annual_median_wage from {s}.fact_wage_benchmark where soc_code='15-1252' and area_type='1' and area_code='99' and survey_year=2025"
        ).fetchone()
        assert wage is not None
        fiscal_groups = c.execute(
            f"select fiscal_year,count(distinct case_number),avg(case when is_certified then 1.0 else 0.0 end) from {s}.fact_lca_case group by 1 order by 1"
        ).fetchall()
        oracle = {
            "fiscal_groups": [
                {"fiscal_year": int(year), "case_count": int(count), "certified_rate": float(rate)}
                for year, count, rate in fiscal_groups
            ],
            "lca_groups": group_oracle,
            # Same numerator / denominator form as the Snowflake oracle, so both compare exactly.
            "lca": lca_oracle(row),
            "postings": c.execute(
                f"select count(*) from {s}.fact_job_posting_daily where is_active"
            ).fetchone()[0],
            "oews_median": float(wage[0]),
            "wage_filters": [
                {"member": f"wages.{k}", "operator": "equals", "values": [v]}
                for k, v in [("soc", "15-1252"), ("area", "1:99"), ("survey_year", "2025")]
            ],
        }
    finally:
        c.close()
    after = fingerprint(source) if kind == "actual" else None
    assert before == after
    (work / "oracle.json").write_text(json.dumps(oracle, indent=2) + "\n")
    evidence = {
        "kind": "ACTUAL_PUBLIC_MARTS" if kind == "actual" else "SYNTHETIC_ANALYTICS_TEST_ONLY",
        "python_duckdb": duckdb.__version__,
        "source_before": before,
        "source_after": after,
        "exports": exports,
        "oracle": oracle,
    }
    (work / "export-evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "kind": evidence["kind"],
                "rows": {x["source_table"]: x["rows"] for x in exports},
                "parquet_bytes": sum(x["size"] for x in exports),
                "oracle": oracle,
            }
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["fixture", "actual"])
    prepare(p.parse_args().kind)
