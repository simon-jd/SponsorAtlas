"""Fiscal-year backfill from archived official LCA quarters.

`readiness` checks that all four quarters are archived (SHA matches the release
registry) and registered as dbt sources; it writes nothing. `run` rebuilds the local
pipeline from those archives, step by step, into the canonical local databases:
RAW load -> dbt staging -> ER export/train/predict -> dimension inputs -> dbt dimensions
-> fact inputs -> dbt facts. Every step is a subprocess; its wall time, exit code and
log are recorded. Destinations must not exist yet (the loaders refuse to overwrite).
ER predict inherits the archived employer registry (er.registry_archive), so employer IDs
survive a rebuild; `run --initialize-registry` starts new IDs instead.

A labelled run (`run_one(..., label=...)`, DAG conf "label") is a verification: every
destination is a new label directory and the canonical databases and registry stay untouched.
An unlabelled Airflow run rewrites the canonical paths, and the dbt steps rebuild in place, so it
also needs `overwrite_canonical=True`, DAG conf "overwrite_canonical": true.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from orchestration.sponsoratlas_orchestration.runtime import ROOT
from scripts.project_limits import DISK_SHARE_BYTES

PYTHON = str(ROOT / ".venv/bin/python")
DBT = str(ROOT / "dbt/run.sh")
# This project's disk share (scripts/project_limits.py). PG data under .tools/governance is not
# readable by du; its last measured allocation is added as a constant.
SHARE_BYTES = DISK_SHARE_BYTES
UNREADABLE_PG_BYTES = 110 * 10**6
# Estimated growth: the RAW load + staging build of all LCA quarters (707 MB of used
# blocks for 239k LCA rows on 2026-10-27, scaled to 717k rows), and fact inputs + build
# as a multiple of the new staging DB (old facts/staging file ratio 1.72 GB / 1.07 GB).
LOAD_RESERVE_BYTES = 2_100 * 10**6
FACT_STAGING_FACTOR = 1.6
DIMENSION_SELECT = (
    "+dim_employer +dim_occupation +dim_geography +dim_date +int_dimension_mapping "
    "scd2_no_overlap dimension_reference_integrity dimension_mapping_accounting"
).split()
FACT_SELECT = (
    "ref_soc_2018 ref_county_2023 ref_county_cbsa_2023 county_fips file_column_map "
    "dim_occupation dim_geography int_dimension_mapping stg_bls tag:facts "
    "dimension_reference_integrity dimension_mapping_accounting"
).split()
LABEL = re.compile(r"[a-z][a-z0-9-]{0,62}")


def fiscal_year_readiness(year):
    if isinstance(year, bool) or not 2000 <= year <= 2035:
        raise ValueError("Fiscal year must be within the reviewed date dimension (2000..2035)")
    started = time.monotonic()
    releases = json.loads((ROOT / "ingestion/releases.json").read_text())["releases"]
    registered = (ROOT / "dbt/macros/platform.sql").read_text()
    available, missing, rows, coverage = [], [], 0, []
    for quarter in range(1, 5):
        period = f"FY{year}_Q{quarter}"
        configured = next(
            (r for r in releases if r["source"] == "LCA" and r["release"] == period), None
        )
        archives = list((ROOT / "data/raw/lca" / period).glob("*.provenance.json"))
        if (
            configured is None
            or len(archives) != 1
            or f"'release':'{period}'" not in registered
        ):
            missing.append(period)
            continue
        metadata = json.loads(archives[0].read_text())
        original = Path(metadata["original_path"])
        if not original.is_absolute():
            original = ROOT / original
        original = original.resolve()
        if not original.is_relative_to(ROOT / "data/raw/lca"):
            raise ValueError("Unexpected LCA archive path")
        with original.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha != metadata["sha256"] or sha != configured.get("expected_sha256"):
            raise ValueError("Full-fiscal-year input archive integrity mismatch")
        available.append(period)
        rows += metadata["source_rows"]
        observed = configured.get("observed_coverage", {})
        coverage.extend([observed.get("decision_date_start"), observed.get("decision_date_end")])
    dates = sorted(d for d in coverage if d)
    return {
        "status": "BLOCKED" if missing else "READY",
        "fiscal_year": year,
        "requested_interval": [f"{year - 1}-10-01", f"{year}-10-01"],
        "available_releases": available,
        "missing_releases": missing,
        "raw_rows": rows,
        "source_date_coverage": [dates[0], dates[-1]] if dates else [],
        "reason": "missing archived or unregistered quarters" if missing else None,
        "mutations_started": False,
        "seconds": time.monotonic() - started,
    }


def backfill_steps(
    year,
    *,
    staging=None,
    dimensions=None,
    facts=None,
    er=None,
    registry_archive=None,
    previous_registry=None,
    work=None,
    initialize_registry=False,
):
    # New files next to the Q4-only databases: the loaders refuse to overwrite.
    staging = Path(staging or ROOT / f".tools/staging/fy{year}-full.duckdb")
    dimensions = Path(dimensions or ROOT / f".tools/dimensions/fy{year}-full.duckdb")
    facts = Path(facts or ROOT / f".tools/facts/fy{year}-full.duckdb")
    er = Path(er or ROOT / f".tools/er/fy{year}-backfill")
    # Outside the backfill directory, which is moved aside before a rebuild.
    registry_archive = Path(registry_archive or ROOT / f".tools/er/registry-archive-fy{year}")
    work = Path(work or ROOT / f".tools/orchestration/backfill-fy{year}")

    def dbt(database, label, *args, **extra):
        target = work / label
        return {
            "work": target,
            "name": label,
            "argv": [DBT, *args],
            "env": {
                # The deployed environment sets DBT_TARGET=snowflake; this rebuild is local.
                "DBT_TARGET": "local",
                "SPONSORATLAS_DUCKDB_PATH": str(database),
                "DBT_TARGET_PATH": str(target / "target"),
                "DBT_LOG_PATH": str(target / "logs"),
            },
            "run_results": target / "target/run_results.json",
            **extra,
        }

    er_dirs = [
        "--input-dir", str(er / "input"),
        "--model-dir", str(er / "model"),
        "--prediction-dir", str(er / "prediction"),
    ]  # fmt: skip
    predict_paths = ["--input", str(er / "input"), "--model", str(er / "model"),
                     "--output", str(er / "prediction")]  # fmt: skip
    if previous_registry:
        # Reads this one file and publishes nothing, so no archive can change.
        predict = ["er.predict", *predict_paths, "--previous-registry", str(previous_registry)]
    else:
        predict = ["er.registry_archive", *predict_paths, "--archive", str(registry_archive),
                   *(["--initialize-registry"] if initialize_registry else [])]  # fmt: skip
    return [
        {
            "name": "load_raw",
            "argv": [PYTHON, "-m", "scripts.load_staging_files", "--database", str(staging),
                     "--output", str(work / "load.json")],
            "reserve_bytes": LOAD_RESERVE_BYTES,
            "work": work,
            # A load that committed before a later failure is reconciled, not reloaded:
            # every RAW table must hold exactly its archive's row count for its SHA.
            "destination": staging,
            "manifest": work / "manifest.json",
            "resume_argv": [PYTHON, "-m", "scripts.reconcile_counts", str(work / "manifest.json"),
                            "--duckdb", str(staging), "--output", str(work / "reconcile.csv")],
        },  # fmt: skip
        dbt(staging, "dbt_seed", "seed"),
        dbt(staging, "dbt_staging", "build", "--select", "staging", gate_failures_allowed=True),
        {
            "name": "er_export",
            "argv": [PYTHON, "-m", "er.export", "--staging", str(staging),
                     "--output", str(er / "input")],
        },  # fmt: skip
        {
            "name": "er_train",
            "argv": [PYTHON, "-m", "er.train", "--input", str(er / "input"),
                     "--output", str(er / "model"), "--labels", "er/labels/pairs.csv"],
        },  # fmt: skip
        {
            "name": "er_predict",
            "argv": [PYTHON, "-m", *predict],
        },
        {
            "name": "dimension_inputs",
            "argv": [PYTHON, "-m", "scripts.load_dimension_inputs", "--database",
                     str(dimensions), *er_dirs, "--source-database", str(staging)],
        },  # fmt: skip
        dbt(dimensions, "dbt_dimensions", "--warn-error", "build",
            "--indirect-selection", "cautious", "--select", *DIMENSION_SELECT),  # fmt: skip
        {
            "name": "fact_inputs",
            "argv": [PYTHON, "-m", "scripts.load_fact_inputs", "--database", str(facts),
                     "--staging", str(staging), "--dimensions", str(dimensions), *er_dirs],
            "reserve_staging_factor": FACT_STAGING_FACTOR,
            "staging": staging,
        },  # fmt: skip
        dbt(facts, "dbt_facts", "--warn-error", "build",
            "--indirect-selection", "cautious", "--select", *FACT_SELECT),  # fmt: skip
    ]


def labelled_paths(year, label):
    """Where a labelled run writes. Each database gets a label directory inside its own area:
    the loaders refuse anything else (load_staging_files.py:111, load_fact_inputs.py:121-126)."""
    if not isinstance(label, str) or not LABEL.fullmatch(label):
        raise ValueError(f"Backfill label must match {LABEL.pattern}, got {label!r}")
    database = Path("backfill-runs") / label / f"fy{year}.duckdb"
    run = ROOT / ".tools/backfill-runs" / label
    return {
        "staging": ROOT / ".tools/staging" / database,
        "dimensions": ROOT / ".tools/dimensions" / database,
        "facts": ROOT / ".tools/facts" / database,
        "er": run / "er",
        "work": run / "work",
    }


def labelled_steps(year, label, *, archive=None):
    """The steps of a labelled run. ER inherits the canonical registry's newest file read only."""
    paths = labelled_paths(year, label)
    archive = Path(archive or ROOT / f".tools/er/registry-archive-fy{year}")
    previous = archive / (archive / "LATEST").read_text().strip()
    return backfill_steps(year, **paths, previous_registry=previous)


def steps_from(steps, start):
    """The named step and every step after it (None: all), to rebuild what depends on it."""
    if start is None:
        return steps
    names = [s["name"] for s in steps]
    if start not in names:
        raise ValueError(f"unknown backfill step {start!r}; one of {names}")
    return steps[names.index(start) :]


def _failed_nodes(run_results):
    """Return (failed tests, failed non-tests) from a dbt run_results.json."""
    results = json.loads(Path(run_results).read_text())["results"]
    bad = [r["unique_id"] for r in results if r["status"] not in {"success", "pass", "warn"}]
    return (
        [u for u in bad if u.startswith("test.")],
        [u for u in bad if not u.startswith("test.")],
    )


def official_manifest():
    """LCA/USCIS archive sidecars, the same set scripts.load_staging_files loads."""
    sidecars = sorted((ROOT / "data/raw").glob("*/*/*.provenance.json"))
    archives = [json.loads(p.read_text()) for p in sidecars]
    return [meta for meta in archives if meta.get("source") in {"LCA", "USCIS"}]


def project_bytes():
    """Allocated bytes of the checkout and its caches (du exits 1 on the unreadable PG dir)."""
    out = subprocess.run(
        ["du", "-s", "-B1", str(ROOT)], capture_output=True, text=True, check=False
    ).stdout
    return int(out.split()[0]) + UNREADABLE_PG_BYTES


def _projected(step):
    reserve = step.get("reserve_bytes", 0)
    if "reserve_staging_factor" in step:
        reserve += int(step["reserve_staging_factor"] * Path(step["staging"]).stat().st_size)
    return project_bytes() + reserve if reserve else None


def run_steps(steps, output, *, logs):
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    result = {
        "status": "SUCCEEDED",
        "nproc": os.cpu_count(),
        "load_before": list(os.getloadavg()),
        "steps": [],
    }
    started = time.monotonic()
    for step in steps:
        resumed = "resume_argv" in step and Path(step["destination"]).exists()
        projected = None if resumed else _projected(step)
        if projected is not None and projected > SHARE_BYTES:
            result["steps"].append(
                {"name": step["name"], "exit_code": None, "projected_bytes": projected,
                 "share_bytes": SHARE_BYTES}
            )  # fmt: skip
            result["status"] = "BLOCKED_DISK_SHARE"
            break
        if "work" in step:
            Path(step["work"]).mkdir(parents=True, exist_ok=True)
        argv = step["argv"]
        if resumed:
            argv = step["resume_argv"]
            if "manifest" in step:
                Path(step["manifest"]).write_text(json.dumps(official_manifest(), indent=2))
        begin = time.monotonic()
        with (logs / f"{step['name']}.log").open("w") as log:
            code = subprocess.run(
                argv,
                cwd=ROOT,
                env={**os.environ, **step.get("env", {})},
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            ).returncode
        record = {
            "name": step["name"],
            "exit_code": code,
            "seconds": round(time.monotonic() - begin, 3),
            "load_after": list(os.getloadavg()),
            **({"projected_bytes": projected} if projected is not None else {}),
            **({"resumed": True} if resumed else {}),
        }
        result["steps"].append(record)
        if code != 0 and step.get("gate_failures_allowed") and Path(step["run_results"]).exists():
            tests, others = _failed_nodes(step["run_results"])
            record["failed_tests"] = tests
            if tests and not others:
                result["status"] = "COMPLETED_WITH_FAILED_GATES"
                continue
        if code != 0:
            result["status"] = "FAILED"
            break
    result["seconds"] = round(time.monotonic() - started, 3)
    Path(output).write_text(json.dumps(result, indent=2) + "\n")
    return result


def run_one(year, name, evidence, label=None, overwrite_canonical=False):
    """Airflow task body: run one named step; raise unless it succeeded (or only gates failed).

    With a label the step uses labelled_steps and its own evidence directory. Without one it
    refuses unless the conf says true, so a missing or mistyped label cannot reach canonical paths.
    """
    if label is None:
        if overwrite_canonical is not True:
            raise ValueError(
                'Backfill conf needs {"label": "verify-YYYYMMDD"}, or '
                '{"overwrite_canonical": true} to rewrite the canonical databases'
            )
        steps = backfill_steps(year)
    else:
        steps = labelled_steps(year, label)
        for area in ("staging", "dimensions", "facts"):
            labelled_paths(year, label)[area].parent.mkdir(parents=True, exist_ok=True)
        evidence = ROOT / f"docs/evidence/task09-backfill-airflow-{label}"
    step = next(s for s in steps if s["name"] == name)
    evidence = Path(evidence)
    result = run_steps([step], evidence / f"{name}.json", logs=evidence / "logs")
    if result["status"] in {"FAILED", "BLOCKED_DISK_SHARE"}:
        raise RuntimeError(f"backfill step {name} failed: {result['steps'][-1]}")
    return result["status"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["readiness", "run"])
    parser.add_argument("--fiscal-year", type=int, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--logs", type=Path)
    parser.add_argument("--from-step", help="rerun this step and everything after it")
    parser.add_argument(
        "--initialize-registry",
        action="store_true",
        help="start new employer IDs instead of inheriting the archived registry",
    )
    args = parser.parse_args()
    readiness = fiscal_year_readiness(args.fiscal_year)
    if args.command == "readiness" or readiness["status"] != "READY":
        print(json.dumps(readiness, indent=2))
        raise SystemExit(0 if readiness["status"] == "READY" else 2)
    if not args.output or not args.logs:
        parser.error("run needs --output and --logs")
    steps = steps_from(
        backfill_steps(args.fiscal_year, initialize_registry=args.initialize_registry),
        args.from_step,
    )
    result = run_steps(steps, args.output, logs=args.logs)
    result["readiness"] = readiness
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "readiness"}, indent=2))
    raise SystemExit(0 if result["status"] in {"SUCCEEDED", "COMPLETED_WITH_FAILED_GATES"} else 1)


if __name__ == "__main__":
    main()
