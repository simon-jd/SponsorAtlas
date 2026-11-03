"""Load the inputs of the local full-year build into Snowflake RAW as AIRBYTE_SVC / LOADER.

A script, not Airbyte: each file goes to its table's own stage (PUT) and is loaded with
COPY INTO; the table is then counted and fingerprinted on both sides.

* Archived official files (JSONL envelopes next to their provenance sidecars): LCA FY2024 Q4
  and FY2025 Q1-Q4, USCIS FY2022/FY2023, BLS May 2025. BLS_FY2024 is created empty, as in
  the local fact build.
* Inputs the local build hands to dbt outside dbt, exported read-only from the canonical
  DuckDB files to Parquet: the synthetic RAW_JOBS fixtures, the entity-resolution output
  (DIMENSIONS_INPUT, at er.settings.THRESHOLD) and FACTS_INPUT. Entity resolution is not run here.

Fingerprint: SUM of the first 32 bits of MD5 over a few columns joined with '|', computed
by DuckDB on the local source and by Snowflake on the loaded table.

    python -m scripts.load_snowflake_raw --output load.json [--only TABLE ...] [--replace]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import duckdb

from scripts.canonical_paths import FACTS_DB, STAGING_DB

ROOT = Path(__file__).resolve().parents[1]
TABLE = re.compile(r"RAW\.(LCA|USCIS|BLS|JOBS)\.([A-Z][A-Z0-9_]{0,200})\Z")
COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,200}\Z")
ENVELOPE = [
    ("SOURCE_RELEASE", "VARCHAR"),
    ("SOURCE_SHA256", "VARCHAR"),
    ("SOURCE_URL", "VARCHAR"),
    ("SOURCE_ROW_NUMBER", "NUMBER(38,0)"),
    ("PAYLOAD_JSON", "VARCHAR"),
]
ENVELOPE_FINGERPRINT = ["source_row_number", "payload_json"]
INTEGERS = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT"}
SIMPLE_TYPES = {
    "VARCHAR": "VARCHAR",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "DOUBLE": "FLOAT",
    "FLOAT": "FLOAT",
    "TIMESTAMP": "TIMESTAMP_NTZ",
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMP_TZ",
}
# (local database, schema, table, Snowflake target, fingerprint columns)
LOCAL_INPUTS = [
    *[
        (STAGING_DB, "RAW_JOBS", t, f"RAW.JOBS.{t}", fp)
        for t, fp in [
            ("ACME_GH_GREENHOUSE", ["job_key", "payload_json"]),
            ("ACME_LV_LEVER", ["job_key", "payload_json"]),
            ("ACME_AB_ASHBY", ["job_key", "payload_json"]),
            ("CDC_JOB_POSTINGS", ["id", "title", "_ab_cdc_lsn"]),
            ("CDC_APPLICATION_EVENTS", ["id", "event_type", "_ab_cdc_lsn"]),
        ]
    ],
    (FACTS_DB, "DIMENSIONS_INPUT", "employer_sources", "RAW.LCA.DIMENSIONS_INPUT_EMPLOYER_SOURCES",
     ["unique_id", "source_system", "source_employer_key", "employer_id", "source_name"]),
    (FACTS_DB, "DIMENSIONS_INPUT", "lca_attributes", "RAW.LCA.DIMENSIONS_INPUT_LCA_ATTRIBUTES",
     ["source_sha256", "source_row_number", "soc_code", "county_fips", "decision_date"]),
    (FACTS_DB, "DIMENSIONS_INPUT", "lineage", "RAW.LCA.DIMENSIONS_INPUT_LINEAGE", ["manifest_json"]),
    (FACTS_DB, "FACTS_INPUT", "lca", "RAW.LCA.FACTS_INPUT_LCA",
     ["source_sha256", "source_row_number", "payload_json", "case_number", "decision_date"]),
    (FACTS_DB, "FACTS_INPUT", "lineage", "RAW.LCA.FACTS_INPUT_LINEAGE", ["manifest_json"]),
    # One RAW schema per dbt source: dbt sources cannot set a schema per table.
    (FACTS_DB, "FACTS_INPUT", "job_observations", "RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS",
     ["job_key", "source_sha256", "payload_json"]),
]  # fmt: skip


def check_table(table: str) -> tuple[str, str, str]:
    if not TABLE.fullmatch(table):
        raise ValueError(f"unexpected RAW target {table!r}")
    return tuple(table.split("."))


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name


def fingerprint_sql(columns, *, snowflake: bool) -> str:
    for c in columns:
        if not COLUMN.fullmatch(c):
            raise ValueError(f"bad column {c!r}")
    if snowflake:
        joined = " || '|' || ".join(f"COALESCE({c.upper()}::VARCHAR, '')" for c in columns)
        return f"SUM(TO_NUMBER(UPPER(SUBSTR(MD5({joined}), 1, 8)), 'XXXXXXXX'))"
    joined = " || '|' || ".join(f"coalesce(cast({c} as varchar), '')" for c in columns)
    return f"sum(('0x' || substr(md5({joined}), 1, 8))::bigint)"


def snowflake_type(duck: str) -> str:
    duck = duck.upper()
    if duck in INTEGERS:
        return "NUMBER(38,0)"
    if duck in SIMPLE_TYPES:
        return SIMPLE_TYPES[duck]
    if m := re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", duck):
        return f"NUMBER({m[1]},{m[2]})"
    raise ValueError(f"no Snowflake mapping for DuckDB type {duck}")


def envelope_item(meta: dict) -> dict:
    """An archived JSONL envelope; hashes are checked against its provenance sidecar."""
    path = Path(meta["path"])
    if "original_path" in meta and sha256(Path(meta["original_path"])) != meta["sha256"]:
        raise ValueError("original sha256 differs from provenance")
    file_sha = sha256(path)
    if file_sha != meta["derived_sha256"]:
        raise ValueError("derived sha256 differs from provenance")
    with duckdb.connect(":memory:") as c:
        rows, bad, fp = c.execute(
            "select count(*), count(*) filter (where source_release is distinct from ? or"
            " source_sha256 is distinct from ?), " + fingerprint_sql(ENVELOPE_FINGERPRINT, snowflake=False)
            + " from read_json(?, format='newline_delimited', columns={source_release:'VARCHAR',"
            "source_sha256:'VARCHAR',source_url:'VARCHAR',source_row_number:'BIGINT',payload_json:'VARCHAR'})",
            [meta["release"], meta["sha256"], str(path)],
        ).fetchone()  # fmt: skip
    if rows != meta["source_rows"] or bad:
        raise ValueError("archive rows or provenance differ from the sidecar")
    return {
        "table": ".".join(check_table(meta["table"])),
        "kind": "envelope",
        "source": relative(path),
        "path": str(path.resolve()),
        "release": meta["release"],
        "original_sha256": meta["sha256"],
        "file_sha256": file_sha,
        "expected_rows": rows,
        "columns": ENVELOPE,
        "fingerprint": ENVELOPE_FINGERPRINT,
        "local_fingerprint": int(fp) if fp is not None else None,
    }


def empty_envelope_item(table: str, release: str) -> dict:
    return {
        "table": ".".join(check_table(table)),
        "kind": "empty",
        "source": "none (empty, as RAW_BLS.BLS_FY2024 in the local fact build)",
        "release": release,
        "expected_rows": 0,
        "columns": ENVELOPE,
        "fingerprint": ENVELOPE_FINGERPRINT,
        "local_fingerprint": None,
    }


def parquet_item(database, schema, table, target, workdir, *, fingerprint) -> dict:
    """Export one local DuckDB table (read-only) to Parquet files of <= ~64 MB for PUT/COPY;
    several files let one COPY use several warehouse threads."""
    path = Path(workdir) / check_table(target)[2]
    path.mkdir(parents=True, exist_ok=True)
    for old in path.glob("*.parquet"):
        old.unlink()
    with duckdb.connect(str(database), read_only=True) as c:
        columns = c.execute(
            "select column_name, data_type from information_schema.columns"
            " where table_schema = ? and table_name = ? order by ordinal_position",
            [schema, table],
        ).fetchall()
        if not columns:
            raise ValueError(f"missing local table {schema}.{table}")
        relation = f'"{schema}"."{table}"'
        rows, fp = c.execute(
            f"select count(*), {fingerprint_sql(fingerprint, snowflake=False)} from {relation}"
        ).fetchone()
        c.execute(
            f"copy (select * from {relation}) to '{path}'"
            " (format parquet, file_size_bytes '64MB', overwrite_or_ignore true)"
        )
    files = sorted(path.glob("*.parquet"))
    return {
        "table": target,
        "kind": "parquet",
        "source": f"{relative(database)}#{schema}.{table}",
        "path": str(path.resolve() / "*.parquet"),
        "file_sha256": {f.name: sha256(f) for f in files},
        "expected_rows": rows,
        "columns": [(name.upper(), snowflake_type(kind)) for name, kind in columns],
        "fingerprint": fingerprint,
        "local_fingerprint": int(fp) if fp is not None else None,
    }


def statements(item: dict, *, replace: bool = False) -> list[str]:
    database, schema, name = check_table(item["table"])
    columns = ", ".join(f"{c} {t}" for c, t in item["columns"])
    create = f"CREATE {'OR REPLACE ' if replace else ''}TABLE {item['table']} ({columns})"
    if item["kind"] == "empty":
        return [create]
    stage = f"@{database}.{schema}.%{name}"
    compress = "TRUE" if item["kind"] == "envelope" else "FALSE"
    put = (
        f"PUT 'file://{item['path']}' {stage} AUTO_COMPRESS = {compress} OVERWRITE = TRUE"
        " PARALLEL = 4"
    )
    if item["kind"] == "envelope":
        names = ", ".join(c for c, _ in ENVELOPE)
        select = ", ".join(f"$1:{c.lower()}::{t}" for c, t in ENVELOPE)
        copy = (
            f"COPY INTO {item['table']} ({names}) FROM (SELECT {select} FROM {stage})"
            " FILE_FORMAT = (TYPE = JSON) ON_ERROR = ABORT_STATEMENT PURGE = TRUE"
        )
    else:
        copy = (
            f"COPY INTO {item['table']} FROM {stage}"
            " FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE)"
            " MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE ON_ERROR = ABORT_STATEMENT PURGE = TRUE"
        )
    return [create, put, copy]


def verify_sql(item: dict) -> tuple[str, list]:
    fp = fingerprint_sql(item["fingerprint"], snowflake=True)
    if item["kind"] == "parquet":
        return f"SELECT COUNT(*), 0, {fp} FROM {item['table']}", []
    return (
        "SELECT COUNT(*), COUNT_IF(SOURCE_RELEASE IS DISTINCT FROM %s"
        f" OR SOURCE_SHA256 IS DISTINCT FROM %s), {fp} FROM {item['table']}",
        [item["release"], item.get("original_sha256", "")],
    )


def load_item(connection, item: dict, *, replace: bool = False) -> dict:
    timings, query_ids, copy_result = {}, {}, None
    for sql in statements(item, replace=replace):
        verb = sql.split()[0] if not sql.startswith("CREATE") else "CREATE"
        started = time.monotonic()
        cursor = connection.cursor().execute(sql)
        timings[verb] = round(time.monotonic() - started, 3)
        query_ids[verb] = getattr(cursor, "sfqid", None)
        if verb == "COPY":
            copy_result = [[str(v) for v in row] for row in cursor.fetchall()]
    sql, params = verify_sql(item)
    rows, bad, fp = connection.cursor().execute(sql, params).fetchone()
    # COUNT_IF over an empty table is NULL in Snowflake.
    rows, bad, fp = int(rows), int(bad or 0), (int(fp) if fp is not None else None)
    ok = rows == item["expected_rows"] and not bad and fp == item["local_fingerprint"]
    return {
        **{k: item[k] for k in ("table", "kind", "source", "expected_rows")},
        **{k: item[k] for k in ("release", "original_sha256", "file_sha256") if k in item},
        "snowflake_rows": rows,
        "provenance_mismatches": bad,
        "local_fingerprint": item["local_fingerprint"],
        "snowflake_fingerprint": fp,
        "status": "MATCH" if ok else "MISMATCH",
        "seconds": timings,
        "query_ids": query_ids,
        "copy_result": copy_result,
    }


def plan(archive_root: Path, workdir: Path, only=None) -> list[dict]:
    wanted = set(only or [])
    items = []
    for sidecar in sorted(Path(archive_root).glob("*/*/*.provenance.json")):
        meta = json.loads(sidecar.read_text())
        if meta.get("source") in {"LCA", "USCIS", "BLS"} and (
            not wanted or meta["table"] in wanted
        ):
            items.append(envelope_item(meta))
    if not wanted or "RAW.BLS.BLS_FY2024" in wanted:
        items.append(empty_envelope_item("RAW.BLS.BLS_FY2024", "FY2024"))
    for database, schema, table, target, fp in LOCAL_INPUTS:
        if not wanted or target in wanted:
            items.append(parquet_item(database, schema, table, target, workdir, fingerprint=fp))
    if wanted - {i["table"] for i in items}:
        raise ValueError(f"unknown --only targets: {sorted(wanted - {i['table'] for i in items})}")
    return items


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--workdir", type=Path, default=ROOT / ".cache/snowflake-raw-load")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--replace", action="store_true", help="CREATE OR REPLACE the targets")
    args = parser.parse_args(argv)
    items = plan(args.archive_root, args.workdir, args.only)
    from scripts.snowflake_live import connect

    results, started = [], time.monotonic()
    with connect("LOADER", query_tag="sponsoratlas:phase8:raw-load") as conn:
        for item in items:
            print(f"loading {item['table']} ({item['expected_rows']} rows)", file=sys.stderr)
            results.append(load_item(conn, item, replace=args.replace))
            args.output.write_text(json.dumps({"tables": results}, indent=2) + "\n")
    summary = {
        "loader": "scripts.load_snowflake_raw (PUT to table stage + COPY INTO), not Airbyte",
        "identity": "AIRBYTE_SVC / LOADER",
        "wall_seconds": round(time.monotonic() - started, 1),
        "tables": results,
    }
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    for r in results:
        print(f"{r['status']}\t{r['table']}\t{r['expected_rows']}\t{r['snowflake_rows']}")
    return int(any(r["status"] != "MATCH" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
