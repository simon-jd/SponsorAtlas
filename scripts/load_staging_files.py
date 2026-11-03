"""Load complete official LCA/USCIS envelopes into an isolated local DB; BLS/jobs synthetic."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from scripts.local_duckdb import connect_local
from scripts.staging_fixture import ROOT, create_fixture

EXPECTED_RELEASES = frozenset(
    {
        ("LCA", "FY2024_Q4"),
        ("LCA", "FY2025_Q1"),
        ("LCA", "FY2025_Q2"),
        ("LCA", "FY2025_Q3"),
        ("LCA", "FY2025_Q4"),
        ("USCIS", "FY2022"),
        ("USCIS", "FY2023"),
    }
)


def validate_archives(archives: list[dict]) -> None:
    """Reject ambiguous or incomplete revisions before creating any database."""
    if not all(isinstance(meta, dict) for meta in archives):
        raise ValueError("Every archive must have source/release/hash metadata")
    keys = [(meta.get("source"), meta.get("release")) for meta in archives]
    if any(not isinstance(v, str) for key in keys for v in key):
        raise ValueError("Source and release metadata must be strings")
    if Counter(keys) != Counter({key: 1 for key in EXPECTED_RELEASES}):
        raise ValueError(
            "Expected each official (source, release) exactly once; reject missing/duplicate revisions"
        )
    for meta in archives:
        sha = meta.get("sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("Expected lowercase original SHA256 metadata")
        if type(meta.get("source_rows")) is not int or meta["source_rows"] <= 0:
            raise ValueError("Expected positive integer source_rows metadata")
        if not isinstance(meta.get("path"), str) or not Path(meta["path"]).is_file():
            raise ValueError("Expected an existing complete JSONL archive")


def load(database: Path, archives: list[dict]) -> dict:
    validate_archives(archives)
    # This check also ensures cleanup cannot delete a previously existing database.
    if database.exists():
        raise FileExistsError(database)
    create_fixture(database, include_lca_uscis_fixtures=False)
    counts = []
    try:
        with connect_local(database) as conn:
            conn.begin()
            for meta in archives:
                release = meta["release"]
                table = f"RAW_{meta['source']}.{meta['source']}_{release}"
                conn.execute(
                    f"insert into {table} select * from read_json(?,format='newline_delimited',columns={{source_release:'VARCHAR',source_sha256:'VARCHAR',source_url:'VARCHAR',source_row_number:'BIGINT',payload_json:'VARCHAR'}})",
                    [meta["path"]],
                )
                count, invalid_provenance = conn.execute(
                    f"select count(*),count(*) filter (where source_release is null or source_release != ? or source_sha256 is null or source_sha256 != ?) from {table}",
                    [release, meta["sha256"]],
                ).fetchone()
                if count != meta["source_rows"] or invalid_provenance:
                    raise ValueError(
                        "RAW count/release/original hash differs from archive metadata"
                    )
                counts.append(
                    {
                        "source": meta["source"],
                        "release": release,
                        "raw_rows": count,
                        "archived_rows": meta["source_rows"],
                        "original_sha256": meta["sha256"],
                    }
                )
            conn.commit()
    except Exception:
        database.unlink(missing_ok=True)
        Path(str(database) + ".wal").unlink(missing_ok=True)
        spill = Path(str(database) + ".tmp")
        if spill.exists():
            shutil.rmtree(spill)
        raise
    return {
        "dataset": "COMPLETE_OFFICIAL_LCA_USCIS_PLUS_SYNTHETIC_BLS_JOBS",
        "sources": counts,
        "database": str(database),
    }


def official_archives(root: Path) -> list[dict]:
    """LCA/USCIS sidecars only; BLS RAW is loaded by scripts.load_fact_inputs."""
    archives = [json.loads(p.read_text()) for p in sorted(Path(root).glob("*/*/*.provenance.json"))]
    return [meta for meta in archives if meta.get("source") in {"LCA", "USCIS"}]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, default=ROOT / "data/raw")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_relative_to(ROOT / ".tools/staging"):
        parser.error("Local DB must stay inside project .tools/staging")
    if args.output.exists():
        parser.error("Output must be a new evidence file; refusing stale/overwritten claims")
    archives = official_archives(args.archive_root)
    try:
        result = load(database, archives)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
