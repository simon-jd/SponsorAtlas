"""Run real dbt builds and rejection gates against a labeled synthetic DuckDB database."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.local_duckdb import connect_local
from scripts.staging_fixture import ROOT, create_fixture


def verify(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    database = output / "fixture.duckdb"
    create_fixture(database)
    executable = Path(sys.executable).parent / "dbt"
    env = {
        **os.environ,
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DBT_TARGET": "local",
        "SPONSORATLAS_DUCKDB_PATH": str(database.resolve()),
        "DBT_LOG_PATH": str((output / "logs").resolve()),
        "DBT_TARGET_PATH": str((output / "target").resolve()),
    }
    evidence = {
        "dataset": "SYNTHETIC_ONLY",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "database": str(database.resolve()),
        "runs": [],
    }

    def run(label: str, args: list[str], expected_failure: str | None = None) -> None:
        cmd = [str(executable), *args, "--project-dir", "dbt", "--profiles-dir", "dbt"]
        proc = subprocess.run(
            cmd, check=False, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180
        )
        (output / f"{label}.log").write_text(proc.stdout + proc.stderr)
        results_path = output / "target/run_results.json"
        assert results_path.exists(), proc.stdout + proc.stderr
        results = json.loads(results_path.read_text())
        shutil.copyfile(results_path, output / f"{label}.run_results.json")
        failures = [r["unique_id"] for r in results["results"] if r["status"] == "fail"]
        errors = [r["unique_id"] for r in results["results"] if r["status"] == "error"]
        evidence["runs"].append(
            {
                "label": label,
                "command": cmd,
                "exit_code": proc.returncode,
                "counts": {
                    s: sum(r["status"] == s for r in results["results"])
                    for s in ["pass", "success", "fail", "error", "skipped"]
                },
                "failing_tests": failures,
                "errors": errors,
            }
        )
        (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        assert not errors, proc.stdout + proc.stderr
        if expected_failure:
            assert proc.returncode != 0 and any(expected_failure in f for f in failures), (
                proc.stdout
            )
        else:
            assert proc.returncode == 0, proc.stdout + proc.stderr

    run("seed", ["seed"])
    run("green", ["build", "--select", "staging"])
    with connect_local(database) as conn:
        rows = conn.execute(
            "select fiscal_year,annual_wage,soc_code,state_fips,county_fips,cast(received_date as varchar) from STAGING.stg_lca order by fiscal_year"
        ).fetchall()
        assert rows == [
            (2024, Decimal("104000.00"), "15-1252", "06", "06075", "2024-01-02"),
            (2025, Decimal("104000.04"), "15-1252", "29", "29183", "2025-01-02"),
        ]
        assert conn.execute("select job_id from STAGING.stg_cdc_job_postings").fetchall() == [
            ("live",)
        ]
        assert conn.execute(
            "select event_id from STAGING.stg_cdc_application_events"
        ).fetchall() == [("live",)]
        assert conn.execute("select count(*) from STAGING.stg_board_jobs").fetchone()[0] == 3
        assert (
            conn.execute(
                "select count(*) from STAGING.stg_board_jobs where source_provider='ashby'"
            ).fetchone()[0]
            == 1
        )  # isListed=false retained
        originals = {
            family: conn.execute(
                f"select payload_json from RAW_{family}.{family}_FY{2022 if family == 'USCIS' else 2024}{'_Q4' if family == 'LCA' else ''}"
            ).fetchone()[0]
            for family in ["LCA", "USCIS", "BLS"]
        }

    def mutate_fields(
        family: str, changes: dict, label: str, expected: str | None, query: str = ""
    ):
        table = f"RAW_{family}.{family}_FY{2022 if family == 'USCIS' else 2024}{'_Q4' if family == 'LCA' else ''}"
        payload = json.loads(originals[family])
        payload.update(changes)
        with connect_local(database) as conn:
            conn.execute(f"update {table} set payload_json=?", [json.dumps(payload)])
        try:
            run(label, ["build", "--select", "staging"], expected)
            if query:
                with connect_local(database) as conn:
                    return conn.execute(query).fetchall()
        finally:
            with connect_local(database) as conn:
                conn.execute(f"update {table} set payload_json=?", [originals[family]])

    def mutate_payload(family: str, key: str, value: str, label: str, expected: str) -> None:
        mutate_fields(family, {key: value}, label, expected)

    # Integer fields must reject raw fractions before any numeric rounding.
    for value, suffix in [("1.001", "fraction"), ("-0.001", "negative_fraction")]:
        mutate_payload(
            "LCA", "TOTAL_WORKER_POSITIONS", value, f"reject_worker_{suffix}", "staging_quality"
        )
        for column in [
            "Initial Approval",
            "Initial Denial",
            "Continuing Approval",
            "Continuing Denial",
        ]:
            label = column.lower().replace(" ", "_")
            mutate_payload("USCIS", column, value, f"reject_{label}_{suffix}", "staging_quality")
        mutate_payload(
            "BLS", "TOT_EMP", value, f"reject_bls_employment_{suffix}", "staging_quality"
        )
    mutate_payload(
        "USCIS", "Fiscal Year", "2022.001", "reject_fractional_fiscal_year", "staging_quality"
    )

    for family in ["LCA", "USCIS", "BLS"]:
        mutate_payload(
            family,
            "UNREVIEWED_NEW_HEADER",
            "drift",
            f"reject_{family.lower()}_key",
            "unmapped_columns",
        )
    mutate_payload(
        "LCA",
        "WAGE_UNIT_OF_PAY",
        "Fortnight",
        "reject_wage_unit",
        "accepted_values_stg_lca_wage_unit",
    )
    mutate_payload("LCA", "RECEIVED_DATE", "2024-02-30", "reject_date", "staging_quality")
    # Unresolved SOC/county/employer rows are kept and flagged; one flagged row in a
    # one-row fiscal year exceeds every limit in staging_flag_limits.
    mutate_payload("LCA", "SOC_CODE", "15-12INVALID", "reject_soc", "staging_flag_limits")
    mutate_payload(
        "LCA", "WORKSITE_COUNTY", "Unknown County", "reject_county", "staging_flag_limits"
    )
    mutate_fields(
        "LCA",
        {"WORKSITE_STATE": "MO", "WORKSITE_COUNTY": "", "WORKSITE_CITY": "Kansas City"},
        "reject_multi_county_city",
        "staging_flag_limits",
    )
    mutate_payload("USCIS", "Employer", "", "reject_uscis_employer", "staging_flag_limits")
    mutate_payload("USCIS", "State", "XX", "reject_uscis_state", "staging_flag_limits")
    # A blank state, city and ZIP is excluded from the release. It stays in staging
    # and must not consume the 5% state_unresolved limit.
    discarded = mutate_fields(
        "USCIS",
        {"State": "", "City": "", "ZIP": ""},
        "discard_blank_uscis_address",
        None,
        "select released, state_unresolved from STAGING.stg_uscis where fiscal_year = 2022",
    )
    assert discarded == [(False, True)], discarded
    filled = mutate_fields(
        "LCA",
        {"WORKSITE_COUNTY": "", "WORKSITE_CITY": " san  francisco "},
        "county_from_city",
        None,
        "select county_fips, geography_status, county_match_rule, geo_unresolved"
        " from STAGING.stg_lca where fiscal_year = 2024",
    )
    assert filled == [("06075", "MATCHED_FROM_CITY", "PLACE_UNIQUE", False)], filled
    # A multi-county city resolves through its worksite ZIP (ZIP+4 and lost leading zeros
    # included); the ZIP rule runs before the city rule.
    geo_query = (
        "select county_fips, geography_status, county_match_rule, geo_unresolved"
        " from STAGING.stg_lca where fiscal_year = 2024"
    )
    for label, state, city, zip_code, expected in [
        ("county_from_zip", "MO", "Kansas City", "64106-1234", "29095"),
        ("county_from_zip_padded", "MA", "Boston", "2139", "25017"),
    ]:
        filled = mutate_fields(
            "LCA",
            {
                "WORKSITE_STATE": state,
                "WORKSITE_COUNTY": "",
                "WORKSITE_CITY": city,
                "WORKSITE_POSTAL_CODE": zip_code,
            },
            label,
            None,
            geo_query,
        )
        assert filled == [(expected, "MATCHED_FROM_ZIP", "ZCTA_UNIQUE", False)], filled
    # A ZIP from another state (Kansas City, KS) must not fill a Missouri worksite.
    mutate_fields(
        "LCA",
        {
            "WORKSITE_STATE": "MO",
            "WORKSITE_COUNTY": "",
            "WORKSITE_CITY": "Kansas City",
            "WORKSITE_POSTAL_CODE": "66101",
        },
        "reject_zip_other_state",
        "staging_flag_limits",
    )
    with connect_local(database) as conn:
        conn.execute("alter table RAW_LCA.LCA_FY2024_Q4 add column UNREVIEWED_PHYSICAL varchar")
    try:
        run("reject_physical", ["build", "--select", "staging"], "unmapped_columns")
    finally:
        with connect_local(database) as conn:
            conn.execute("alter table RAW_LCA.LCA_FY2024_Q4 drop column UNREVIEWED_PHYSICAL")
    run("restored_green", ["build", "--select", "staging"])
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".tools/staging" / datetime.now(UTC).strftime("checks-%Y%m%dT%H%M%SZ"),
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".tools/staging"):
        parser.error("CLI evidence must stay in project .tools/staging")
    evidence = verify(output)
    print(
        json.dumps(
            {
                "dataset": evidence["dataset"],
                "runs": len(evidence["runs"]),
                "evidence": str(output / "evidence.json"),
            }
        )
    )


if __name__ == "__main__":
    main()
