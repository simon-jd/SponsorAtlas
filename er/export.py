"""Export only real LCA/USCIS staging, with separate complete occurrence lineage."""

import argparse
import json
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from er.io import ROOT, connect, digest, dump, quote, run_dir
from er.normalize import prepare_record
from er.settings import MAX_RECORDS


def verify_originals(originals):
    verified = json.loads((ROOT / "dbt/source_contracts/verified_releases.json").read_text())[
        "releases"
    ]
    allowed = {(r["source"].lower(), r["release"], r["expected_sha256"]) for r in verified}
    if not originals or any(
        (r["source_system"], r["source_release"], r["source_sha256"]) not in allowed
        for r in originals
    ):
        raise ValueError(
            "real-staging export requires pinned official employer originals, not fixtures"
        )


def export(staging, output):
    out = run_dir(output, create=True)
    c = connect(out / "work.duckdb")
    try:
        c.execute(f"ATTACH {quote(Path(staging).resolve())} AS original (READ_ONLY)")
        c.execute("""CREATE TABLE occurrences AS
          SELECT source_system, source_employer_key, employer_name AS source_name,
                 employer_state AS state, employer_country AS country, employer_city AS city,
                 employer_zip AS postcode, employer_fein AS tax_id,
                 source_release, source_sha256, source_row_number, source_url
          FROM original.staging.stg_lca
          UNION ALL
          SELECT source_system, source_employer_key, employer_name,
                 employer_state, NULL, employer_city, employer_zip, employer_tax_id,
                 source_release, source_sha256, source_row_number, source_url
          FROM original.staging.stg_uscis WHERE released""")
        c.execute("""ALTER TABLE occurrences ADD COLUMN unique_id VARCHAR""")
        c.execute("""UPDATE occurrences SET unique_id = sha256(source_system || ':' ||
          coalesce(nullif(trim(source_employer_key),''), 'MISSING:' || source_sha256 || ':' || source_row_number))""")
        conflict = c.execute("""SELECT count(*) FROM (SELECT unique_id FROM
          (SELECT DISTINCT unique_id, source_name, state, country, city, postcode, tax_id FROM occurrences)
          GROUP BY unique_id HAVING count(*) > 1)""").fetchall()[0][0]
        if conflict:
            raise ValueError(f"{conflict} source keys identify conflicting employer attributes")
        if (
            c.execute("SELECT count(DISTINCT unique_id) FROM occurrences").fetchall()[0][0]
            > MAX_RECORDS
        ):
            raise ValueError("bounded driver record limit exceeded")
        originals = (
            c.sql(
                "SELECT source_system,source_release,source_sha256,count(*) AS rows FROM occurrences GROUP BY ALL ORDER BY ALL"
            )
            .to_arrow_table()
            .to_pylist()
        )
        verify_originals(originals)
        cols = "unique_id, source_system, source_employer_key, source_name, state, country, city, postcode, tax_id"
        rows = (
            c.sql(f"SELECT DISTINCT {cols} FROM occurrences ORDER BY unique_id")
            .to_arrow_table()
            .to_pylist()
        )
        rows = [prepare_record(row) for row in rows]
        pq.write_table(pa.Table.from_pylist(rows), out / "records.parquet", compression="zstd")
        c.execute(
            f"COPY occurrences TO {quote(out / 'occurrences.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        issues = Counter(issue for r in rows for issue in r["issues"].split("|") if issue)
        summary = {
            "kind": "real-staging-lca-uscis",
            "source_database": str(Path(staging).resolve()),
            "rows": c.execute("SELECT count(*) FROM occurrences").fetchall()[0][0],
            "source_records": len(rows),
            "eligible": sum(r["eligible"] for r in rows),
            "issues": dict(issues),
            "records_sha256": digest(out / "records.parquet"),
            "occurrences_sha256": digest(out / "occurrences.parquet"),
            "originals": originals,
        }
        dump(out / "input.json", summary)
        return summary
    finally:
        c.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--staging", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    print(__import__("json").dumps(export(a.staging, a.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
