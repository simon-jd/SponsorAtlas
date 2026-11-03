"""Build the OpenMetadata dbt artifact dirs for the canonical full-year DBs.

manifest.json and run_results.json are copied from the `sponsoratlas_backfill` dbt builds;
catalog.json comes from `dbt docs generate --no-compile` on the read-only profile in
governance/dbt-readonly, so the DBs are only read. Each destination must be new.
Run with .venv/bin/python -m governance.refresh_artifacts.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from governance.catalog import RUNTIME, SERVICES
from scripts.canonical_paths import FACTS_DB, STAGING_DB

ROOT = Path(__file__).resolve().parents[1]
BUILDS = ROOT / ".tools/orchestration/backfill-fy2025"
BUILD_FOR = {STAGING_DB: BUILDS / "dbt_staging/target", FACTS_DB: BUILDS / "dbt_facts/target"}


def plan():
    return [
        {"service": service, "artifacts": RUNTIME / artifacts, "database": physical,
         "build": BUILD_FOR[physical]}
        for service, (artifacts, physical) in SERVICES.items()
        if physical in BUILD_FOR
    ]  # fmt: skip


def docs_generate(*, database, target):
    env = {
        **os.environ,
        "SPONSORATLAS_DUCKDB_PATH": str(database),
        "DBT_TARGET_PATH": str(target),
        "DBT_LOG_PATH": str(target.with_name(target.name + "-logs")),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    with (target.with_name(target.name + "-docs.log")).open("w") as log:
        subprocess.run(
            [str(ROOT / ".venv/bin/dbt"), "docs", "generate", "--no-compile",
             "--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "governance/dbt-readonly")],
            env=env, stdout=log, stderr=subprocess.STDOUT, check=True,
        )  # fmt: skip


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def generate(item):
    target = Path(item["artifacts"])
    target.mkdir(parents=True)  # FileExistsError if it is already there
    copied = {}
    for name in ("manifest.json", "run_results.json"):
        shutil.copyfile(Path(item["build"]) / name, target / name)
        copied[name] = _sha(target / name)
    docs_generate(database=item["database"], target=target)
    return {"service": item["service"], "artifacts": str(target), "database": str(item["database"]),
            "build": str(item["build"]), "copied": copied}  # fmt: skip


def main():
    results = []
    for item in plan():
        result = generate(item)
        result["catalog_sha256"] = _sha(Path(item["artifacts"]) / "catalog.json")
        catalog = json.loads((Path(item["artifacts"]) / "catalog.json").read_text())
        result["catalog_nodes"] = len(catalog["nodes"])
        result["catalog_sources"] = len(catalog["sources"])
        results.append(result)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
