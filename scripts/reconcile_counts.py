"""Compare each source file with its own SHA-scoped RAW count (Snowflake, or a local
DuckDB RAW copy with --duckdb); reconcile_cdc compares CDC source tables with RAW.

Exit 0: all counts match; 1: mismatch; 2: invalid input/service error.
Offline counts are explicitly synthetic and cannot satisfy live acceptance.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from scripts.source_files import iter_rows

TABLE = re.compile(r"RAW\.(LCA|USCIS|BLS|JOBS)\.[A-Z][A-Z0-9_]{0,200}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


def validate_entry(entry):
    if not TABLE.fullmatch(entry.get("table", "")):
        raise ValueError("Expected an uppercase RAW.<source>.<table> identifier")
    if not SHA.fullmatch(entry.get("sha256", "")):
        raise ValueError("Invalid source sha256")
    if not entry.get("release") or not entry.get("path"):
        raise ValueError("release and path are required")


def reconcile(manifest, count_raw):
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("Manifest must be a nonempty list")
    results = []
    scopes = set()
    for entry in manifest:
        validate_entry(entry)
        scope = (entry["table"], entry["sha256"])
        if scope in scopes:
            raise ValueError("Duplicate reconciliation scope")
        scopes.add(scope)
        if "original_path" in entry:
            from scripts.archive_sources import sha256_file

            if sha256_file(entry["original_path"]) != entry["sha256"]:
                raise ValueError("Archived original SHA256 no longer matches provenance")
            if sha256_file(entry["path"]) != entry["derived_sha256"]:
                raise ValueError("Derived artifact SHA256 no longer matches provenance")
        source_rows = sum(1 for _ in iter_rows(entry["path"], entry))
        if "source_rows" in entry and source_rows != entry["source_rows"]:
            raise ValueError("Source count no longer matches archive provenance")
        raw_rows = count_raw(*scope)
        if isinstance(raw_rows, bool) or not isinstance(raw_rows, int) or raw_rows < 0:
            raise ValueError("RAW count must be a nonnegative integer")
        results.append(
            {
                "release": entry["release"],
                "table": entry["table"],
                "sha256": entry["sha256"],
                "source_rows": source_rows,
                "raw_rows": raw_rows,
                "difference": raw_rows - source_rows,
                "status": "MATCH" if raw_rows == source_rows else "MISMATCH",
            }
        )
    return results


def snowflake_connection():
    import snowflake.connector

    # No password fallback. Use the same AIRBYTE_SVC/LOADER key-pair identity as Airbyte.
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user="AIRBYTE_SVC",
        role="LOADER",
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "SPONSORATLAS_XS"),
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"],
        private_key_file_pwd=os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
    )


def snowflake_counter(connection):
    def count(table, sha256):
        validate_entry({"table": table, "sha256": sha256, "path": "_", "release": "_"})
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM IDENTIFIER(%s) WHERE SOURCE_SHA256 = %s", (table, sha256)
            )
            return int(cursor.fetchone()[0])

    return count


def duckdb_counter(path):
    """Count SHA-scoped rows in a local DuckDB RAW copy (RAW.LCA.X -> RAW_LCA.X)."""
    from scripts.local_duckdb import connect_local

    def count(table, sha256):
        validate_entry({"table": table, "sha256": sha256, "path": "_", "release": "_"})
        _, source, name = table.split(".")
        with connect_local(path) as conn:
            return int(
                conn.execute(
                    f'SELECT count(*) FROM "RAW_{source}"."{name}" WHERE source_sha256 = ?',
                    [sha256],
                ).fetchone()[0]
            )

    return count


def reconcile_cdc(source_counts, raw_counts):
    """Compare CDC source table row counts with current (non-tombstoned) RAW rows."""
    from scripts.cdc_pgoutput import TABLES

    results = []
    for source, (raw, _) in sorted(TABLES.items(), key=lambda kv: kv[1][0]):
        src, dst = int(source_counts[source]), int(raw_counts[source])
        results.append(
            {
                "release": "postgres_cdc",
                "table": f"RAW.JOBS.{raw}",
                "sha256": "",
                "source_rows": src,
                "raw_rows": dst,
                "difference": dst - src,
                "status": "MATCH" if src == dst else "MISMATCH",
            }
        )
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--offline-counts",
        type=Path,
        help='Synthetic JSON map {"RAW.LCA.TABLE:sha256": count}; no live evidence',
    )
    parser.add_argument(
        "--duckdb", type=Path, help="Count against a local DuckDB RAW copy instead of Snowflake"
    )
    parser.add_argument("--output", type=Path, help="Optional CSV difference table")
    args = parser.parse_args(argv)
    connection = None
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        # Validate before connecting to external services.
        if not isinstance(manifest, list) or not manifest:
            raise ValueError("Manifest must be a nonempty list")
        for entry in manifest:
            validate_entry(entry)
        if args.offline_counts:
            print("SYNTHETIC OFFLINE COUNTS; not live acceptance", file=sys.stderr)
            counts = json.loads(args.offline_counts.read_text(encoding="utf-8"))
            counter = lambda table, sha: counts[f"{table}:{sha}"]
        elif args.duckdb:
            counter = duckdb_counter(args.duckdb)
        else:
            connection = snowflake_connection()
            counter = snowflake_counter(connection)
        results = reconcile(manifest, counter)
        target = args.output.open("w", encoding="utf-8", newline="") if args.output else sys.stdout
        try:
            writer = csv.DictWriter(target, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        finally:
            if args.output:
                target.close()
        return int(any(x["status"] == "MISMATCH" for x in results))
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns documented error exit code
        # Don't include driver exception text: it can contain connection/secrets.
        print(
            f"ERROR: reconciliation failed ({type(exc).__name__}); "
            "check manifest, source files, and connection configuration",
            file=sys.stderr,
        )
        return 2
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
