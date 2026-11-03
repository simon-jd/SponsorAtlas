"""Bounded LOCAL_ARCHIVE_REPLAY into a new .tools/facts DB, never reviewed artifacts."""

import argparse
import json
import warnings
from pathlib import Path
from unittest.mock import patch

import requests
import yaml

from er.io import digest, quote
from scripts.build_bls_references import JSON_SHA, JSONL, ROOT, SHA
from scripts.canonical_paths import DIMENSIONS_DB, ER_INPUT, ER_MODEL, ER_PREDICTION, STAGING_DB
from scripts.load_dimension_inputs import INPUT, MODEL, PREDICTION
from scripts.load_dimension_inputs import load as load_dimensions
from scripts.local_duckdb import connect_local

DIMENSIONS = DIMENSIONS_DB
STAGING = STAGING_DB


def protected_state(staging=STAGING, dimensions=DIMENSIONS, er_dirs=None):
    paths = [Path(dimensions), Path(staging)]
    areas = [ROOT / "data/raw"] + [
        Path(d) for d in er_dirs or [ER_INPUT, ER_MODEL, ER_PREDICTION]
    ]
    for area in areas:
        paths.extend(p for p in area.rglob("*") if p.is_file())
    return {
        str(p.relative_to(ROOT)): {
            "sha256": digest(p),
            "bytes": p.stat().st_size,
            "mtime_ns": p.stat().st_mtime_ns,
        }
        for p in sorted(set(paths))
    }


def replay_board(meta):
    """Run the existing pinned CDK provider extractor/transformations against exact saved bytes."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=r"^This class is experimental\. Use at your own risk\.$"
        )
        from airbyte_cdk.sources.declarative.concurrent_declarative_source import (
            ConcurrentDeclarativeSource,
        )
    manifest = yaml.safe_load((ROOT / "ingestion/board_api/manifest.yaml").read_text())
    provider, board = meta["provider"], meta["board"]
    manifest["streams"] = [s for s in manifest["streams"] if s["name"] == provider]
    manifest["check"]["stream_names"] = [provider]
    pages = meta["pages"]
    for page in pages:
        if digest(page["path"]) != page["sha256"]:
            raise ValueError("job archive digest mismatch")
    emitted = []
    page_index = 0

    def send(session, request, **kwargs):
        nonlocal page_index
        if request.method != "GET" or page_index >= len(pages):
            raise ValueError("unexpected offline provider request")
        page = pages[page_index]
        page_index += 1
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        response._content = Path(page["path"]).read_bytes()
        return response

    # CDK may prefetch all records, so bind each original record to its archived page
    # by the provider key rather than by emission time or mutable request counter.
    page_by_key = {}
    for page in pages:
        data = json.loads(Path(page["path"]).read_bytes())
        records = data if provider == "lever" else data["jobs"]
        if len(records) != page["rows"]:
            raise ValueError("job archive row count mismatch")
        for record in records:
            raw_key = record["jobUrl"] if provider == "ashby" else str(record["id"])
            key = f"{provider}:{board}:{raw_key}"
            if key in page_by_key:
                raise ValueError("duplicate archived posting key")
            page_by_key[key] = page
    with patch.object(requests.Session, "send", send):
        source = ConcurrentDeclarativeSource(source_config=manifest, config={"board": board})
        stream = source.streams({"board": board})[0]
        for partition in stream.generate_partitions():
            for value in partition.read():
                value = dict(value)
                page = page_by_key[value["job_key"]]
                emitted.append(
                    {
                        **value,
                        "source_url": page["url"],
                        "observed_at_utc": page["observed_at_utc"],
                        "source_sha256": page["sha256"],
                        "source_deleted_at": None,
                        "replay_kind": "LOCAL_ARCHIVE_REPLAY",
                    }
                )
    if page_index != len(pages) or len(emitted) != meta["rows"]:
        raise ValueError("provider parser/archive count mismatch")
    return emitted


def load(
    database,
    *,
    staging=STAGING,
    dimensions=DIMENSIONS,
    input_dir=INPUT,
    model_dir=MODEL,
    prediction_dir=PREDICTION,
):
    database = Path(database).resolve()
    staging, dimensions = Path(staging).resolve(), Path(dimensions).resolve()
    if not database.is_relative_to(ROOT / ".tools/facts"):
        raise ValueError("Task6 destination must be a new .tools/facts database")
    if not staging.is_relative_to(ROOT / ".tools/staging"):
        raise ValueError("source staging database must be inside .tools/staging")
    if not dimensions.is_relative_to(ROOT / ".tools/dimensions"):
        raise ValueError("source dimensions database must be inside .tools/dimensions")
    if database.exists():
        raise FileExistsError(database)
    er_dirs = [input_dir, model_dir, prediction_dir]
    if digest(JSONL) != JSON_SHA or digest(JSONL.with_suffix(".zip")) != SHA:
        raise ValueError("BLS archive digest mismatch")
    before = protected_state(staging, dimensions, er_dirs)
    jobs, job_counts = [], {}
    for provider in ["greenhouse", "lever", "ashby"]:
        meta = json.loads((ROOT / f".tools/ingestion/{provider}-public.json").read_text())
        rows = replay_board(meta)
        jobs.extend(rows)
        job_counts[provider] = len(rows)
    # Use the reviewed identity/registry validator; no recreated or name-joined xref.
    lineage = load_dimensions(
        database,
        input_dir=input_dir,
        model_dir=model_dir,
        prediction_dir=prediction_dir,
        source_database=staging,
    )
    with connect_local(database) as c:
        c.execute("attach " + quote(dimensions) + " as reviewed (read_only)")
        old = json.loads(
            c.execute("select manifest_json from reviewed.DIMENSIONS_INPUT.lineage").fetchone()[0]
        )
        if old["registry_sha256"] != lineage["registry_sha256"]:
            raise ValueError("reviewed dimension registry mismatch")
        for schema in ["MARTS", "SNAPSHOTS", "FACTS_INPUT", "RAW_BLS", "STAGING"]:
            c.execute(f"create schema if not exists {schema}")
        for table in ["dim_employer", "dim_occupation", "dim_geography", "dim_date"]:
            c.execute(f"create table MARTS.{table} as select * from reviewed.MARTS.{table}")
        c.execute(
            "create table SNAPSHOTS.snap_employer as select * from reviewed.SNAPSHOTS.snap_employer"
        )
        c.execute("attach " + quote(staging) + " as original (read_only)")
        c.execute("create table FACTS_INPUT.lca as select * from original.STAGING.stg_lca")
        c.execute(
            "create table RAW_BLS.BLS_FY2025 as select * from read_json(?,format='newline_delimited',columns={source_release:'VARCHAR',source_sha256:'VARCHAR',source_url:'VARCHAR',source_row_number:'BIGINT',payload_json:'VARCHAR'})",
            [str(JSONL)],
        )
        c.execute("create table RAW_BLS.BLS_FY2024 as select * from RAW_BLS.BLS_FY2025 limit 0")
        n, bad = c.execute(
            "select count(*),count(*) filter(where source_sha256 is distinct from ? or source_release is distinct from 'FY2025') from RAW_BLS.BLS_FY2025",
            [SHA],
        ).fetchone()
        if n != 413527 or bad:
            raise ValueError("BLS full source count/provenance mismatch")
        # Explicit schema also supports a legitimately empty provider.
        c.execute(
            "create table FACTS_INPUT.job_observations(job_key varchar,source_provider varchar,source_board varchar,source_url varchar,payload_json varchar,observed_at_utc timestamp,source_sha256 varchar,source_deleted_at timestamp,replay_kind varchar)"
        )
        for row in jobs:
            c.execute(
                "insert into FACTS_INPUT.job_observations values (?,?,?,?,?,cast(? as timestamptz) at time zone 'UTC',?,cast(? as timestamp),?)",
                [
                    row[k]
                    for k in [
                        "job_key",
                        "source_provider",
                        "source_board",
                        "source_url",
                        "payload_json",
                        "observed_at_utc",
                        "source_sha256",
                        "source_deleted_at",
                        "replay_kind",
                    ]
                ],
            )
        c.execute(
            "create table FACTS_INPUT.lineage as select ? as manifest_json",
            [
                json.dumps(
                    {
                        "kind": "LOCAL_ARCHIVE_REPLAY",
                        "dimension_registry_sha256": lineage["registry_sha256"],
                        "bls_original_sha256": SHA,
                        "bls_jsonl_sha256": JSON_SHA,
                        "job_counts": job_counts,
                    }
                )
            ],
        )
    after = protected_state(staging, dimensions, er_dirs)
    if before != after:
        raise AssertionError("reviewed source artifact changed")
    result = {
        "kind": "LOCAL_ARCHIVE_REPLAY",
        "database": str(database),
        "bls_raw_rows": n,
        "job_rows": job_counts,
        "protection_before": before,
        "protection_after": after,
        "database_bytes": database.stat().st_size,
    }
    (database.parent / "load-evidence.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--staging", type=Path, default=STAGING)
    p.add_argument("--dimensions", type=Path, default=DIMENSIONS)
    p.add_argument("--input-dir", type=Path, default=INPUT)
    p.add_argument("--model-dir", type=Path, default=MODEL)
    p.add_argument("--prediction-dir", type=Path, default=PREDICTION)
    a = p.parse_args()
    result = load(
        a.database,
        staging=a.staging,
        dimensions=a.dimensions,
        input_dir=a.input_dir,
        model_dir=a.model_dir,
        prediction_dir=a.prediction_dir,
    )
    print(json.dumps({k: v for k, v in result.items() if not k.startswith("protection")}, indent=2))
