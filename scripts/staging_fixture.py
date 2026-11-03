"""Explicit synthetic RAW fixture for real dbt/DuckDB execution; never Snowflake data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.local_duckdb import connect_local

ROOT = Path(__file__).resolve().parents[1]
ENVELOPE = "source_release varchar, source_sha256 varchar, source_url varchar, source_row_number bigint, payload_json varchar"


def create_fixture(path: Path, *, include_lca_uscis_fixtures: bool = True) -> None:
    """Create a new, isolated synthetic DB. Refuse accidental overwrite."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    with connect_local(path) as conn:
        for family in ["LCA", "USCIS", "BLS", "JOBS"]:
            conn.execute(f"create schema RAW_{family}")
        # FY2025 Q1-Q3 releases have no synthetic rows; the full-file loader fills them.
        for quarter in (1, 2, 3):
            conn.execute(f"create table RAW_LCA.LCA_FY2025_Q{quarter} ({ENVELOPE})")
        for year in (2024, 2025):
            for family in ("LCA", "USCIS", "BLS"):
                release_year = year - 2 if family == "USCIS" else year
                period = f"FY{release_year}_Q4" if family == "LCA" else f"FY{release_year}"
                name = f"{family}_{period}"
                conn.execute(f"create table RAW_{family}.{name} ({ENVELOPE})")
                if not include_lca_uscis_fixtures and family in {"LCA", "USCIS"}:
                    continue
                if family == "LCA":
                    payload = {
                        "CASE_NUMBER": f"SYNTHETIC-{year}",
                        "VISA_CLASS": "H-1B",
                        "TRADE_NAME_DBA": "SYNTHETIC",
                        "EMPLOYER_COUNTRY": "UNITED STATES OF AMERICA",
                        "CASE_STATUS": "Certified",
                        "RECEIVED_DATE": f"{year}-01-02T00:00:00",
                        "DECISION_DATE": f"01/03/{year}",
                        "BEGIN_DATE": f"{year}-02-01",
                        "END_DATE": f"{year + 1}-02-01",
                        "EMPLOYER_NAME": "SYNTHETIC Example LLC",
                        "EMPLOYER_FEIN": "00-0000000",
                        "EMPLOYER_CITY": "San Francisco",
                        "EMPLOYER_POSTAL_CODE": "94103",
                        "EMPLOYER_ADDRESS1": "SYNTHETIC ONLY",
                        "EMPLOYER_STATE": "ca",
                        "JOB_TITLE": "SYNTHETIC engineer",
                        "SOC_CODE": "15-1252.00" if year == 2024 else "151252",
                        "WORKSITE_STATE": "ca" if year == 2024 else "mo",
                        "WORKSITE_COUNTY": "San Francisco" if year == 2024 else "  St   Charles ",
                        "WAGE_RATE_OF_PAY_FROM": "50" if year == 2024 else "8666.67",
                        "WAGE_UNIT_OF_PAY": "Hour" if year == 2024 else "Month",
                        "TOTAL_WORKER_POSITIONS": "1",
                    }
                elif family == "USCIS":
                    payload = {
                        "Fiscal Year": str(release_year),
                        "Employer": "SYNTHETIC Example LLC",
                        "Tax ID": "0000",
                        "State": "CA",
                        "City": "San Francisco",
                        "ZIP": "94103",
                        "Initial Approval": "10",
                        "Initial Denial": "2",
                        "Continuing Approval": "3",
                        "Continuing Denial": "1",
                        "NAICS": "541511",
                    }
                else:
                    payload = {
                        "AREA": "41860",
                        "AREA_TITLE": "SYNTHETIC metro",
                        "AREA_TYPE": "4",
                        "PRIM_STATE": "CA",
                        "OCC_CODE": "15-1252",
                        "OCC_TITLE": "Software Developers",
                        "O_GROUP": "detailed",
                        "A_MEAN": "104000",
                        "H_MEAN": "50",
                        "TOT_EMP": "1000",
                    }
                url = {
                    "LCA": f"https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_{period}.xlsx",
                    "USCIS": "https://www.uscis.gov/SYNTHETIC-NOT-A-RELEASE",
                    "BLS": "https://www.bls.gov/SYNTHETIC-NOT-A-RELEASE",
                }[family]
                sha = hashlib.sha256(f"SYNTHETIC:{name}".encode()).hexdigest()
                conn.execute(
                    f"insert into RAW_{family}.{name} values (?,?,?,?,?)",
                    [period, sha, url, 1, json.dumps(payload)],
                )
        boards = {
            "greenhouse": (
                "ACME_GH_GREENHOUSE",
                {
                    "id": 1,
                    "title": "SYNTHETIC Engineer",
                    "company_name": "SYNTHETIC Example LLC",
                    "location": {"name": "San Francisco, CA"},
                    "absolute_url": "https://example.invalid/gh/1",
                },
            ),
            "lever": (
                "ACME_LV_LEVER",
                {
                    "id": "1",
                    "text": "SYNTHETIC Engineer",
                    "categories": {"location": "San Francisco, CA"},
                    "hostedUrl": "https://example.invalid/lv/1",
                },
            ),
            "ashby": (
                "ACME_AB_ASHBY",
                {
                    "title": "SYNTHETIC Engineer",
                    "location": "San Francisco, CA",
                    "jobUrl": "https://example.invalid/ab/1",
                    "isListed": False,
                },
            ),
        }
        for provider, (table, payload) in boards.items():
            conn.execute(
                f"create table RAW_JOBS.{table} (job_key varchar, source_provider varchar, source_board varchar, source_url varchar, payload_json varchar, _airbyte_extracted_at timestamp)"
            )
            key = payload.get("id", payload.get("jobUrl"))
            conn.execute(
                f"insert into RAW_JOBS.{table} values (?,?,?,?,?,?)",
                [
                    f"{provider}:acme:{key}",
                    provider,
                    "acme",
                    "https://example.invalid/SYNTHETIC",
                    json.dumps(payload),
                    "2026-01-01 00:00:00",
                ],
            )
        metadata = "_ab_cdc_lsn varchar, _ab_cdc_updated_at timestamp, _ab_cdc_deleted_at timestamp, _airbyte_extracted_at timestamp"
        conn.execute(
            f"create table RAW_JOBS.CDC_JOB_POSTINGS (id varchar,title varchar,company_name varchar,updated_at timestamp,{metadata})"
        )
        conn.execute(
            f"create table RAW_JOBS.CDC_APPLICATION_EVENTS (id varchar,job_id varchar,event_type varchar,event_at timestamp,{metadata})"
        )
        for tombstone in (False, True):
            deleted = "2026-01-02 00:00:00" if tombstone else None
            key = "deleted" if tombstone else "live"
            conn.execute(
                "insert into RAW_JOBS.CDC_JOB_POSTINGS values (?,?,?,?,?,?,?,?)",
                [
                    key,
                    "SYNTHETIC Engineer",
                    "SYNTHETIC Example LLC",
                    "2026-01-01",
                    "1/001",
                    "2026-01-01",
                    deleted,
                    "2026-01-02",
                ],
            )
            conn.execute(
                "insert into RAW_JOBS.CDC_APPLICATION_EVENTS values (?,?,?,?,?,?,?,?)",
                [key, key, "submitted", "2026-01-01", "1/001", "2026-01-01", deleted, "2026-01-02"],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=ROOT / ".tools/staging/sponsoratlas.duckdb"
    )
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_relative_to(ROOT / ".tools/staging"):
        parser.error("CLI fixture DB must be inside this project's .tools/staging")
    create_fixture(path)
    print(json.dumps({"dataset": "SYNTHETIC_ONLY", "database": str(path)}))


if __name__ == "__main__":
    main()
