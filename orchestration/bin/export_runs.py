#!/usr/bin/env python
"""Export every DAG run and task instance state, from the Airflow CLI, to the evidence folder.

A run is evidence only when ALL of its task instances succeeded, it started on or after
EVIDENCE_START, and its manifest (guard.record_usage) shows production mode, the snowflake dbt
target and a Snowflake identity. Airflow marks a run "success" when preflight skips everything
downstream, so the run state alone proves nothing. Rehearsals and stub runs have no such manifest.

    orchestration/run.sh python orchestration/bin/export_runs.py   # writes dag-runs.json
    orchestration/bin/status.sh                                    # prints plain text only
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from orchestration.sponsoratlas_orchestration import guard
from orchestration.sponsoratlas_orchestration.runtime import ALERT_LOG, ROOT, safe_run_id

DAG_IDS = ("sponsoratlas_daily", "sponsoratlas")
OUTPUT = guard.EVIDENCE / "dag-runs.json"
RUN_SH = ROOT / "orchestration/run.sh"
EVIDENCE_START = datetime(2026, 11, 6, tzinfo=UTC)
EVIDENCE_RULE = (
    "A run counts as evidence only when every task instance succeeded, it started on or after "
    f"{EVIDENCE_START.isoformat()}, and its run manifest shows orchestration_mode production, "
    "dbt_target snowflake, a Snowflake identity on SPONSORATLAS_XS and, for the daily DAG, a board "
    "load in snowflake mode. Other runs are rehearsals or stubs."
)


def airflow_lines(cli, *args):
    done = subprocess.run(
        [str(cli), "airflow", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return done.stdout.splitlines()


def airflow_json(cli, *args):
    """The CLI prints warnings to stdout too; its JSON is the last line that parses as a list."""
    for line in reversed(airflow_lines(cli, *args, "--output", "json")):
        if line.startswith("["):
            return json.loads(line)
    raise RuntimeError(f"No JSON in airflow {' '.join(args)}")


def run_manifest(dag_id, run_id):
    path = guard.EVIDENCE / "runs" / f"{safe_run_id(run_id)}.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text())
    return manifest if manifest.get("dag_id") == dag_id else None


def ran_on_snowflake(manifest):
    if not manifest:
        return False
    identity = (manifest.get("metered_total") or {}).get("identity") or {}
    boards = manifest.get("board_manifest_totals") or {}
    return (
        manifest.get("orchestration_mode") == "production"
        and manifest.get("dbt_target") == "snowflake"
        and bool(identity.get("account"))
        and identity.get("warehouse") == "SPONSORATLAS_XS"
        and (manifest.get("dag_id") != "sponsoratlas_daily" or boards.get("mode") == "snowflake")
    )


def summarize_run(run, task_instances, manifest=None):
    states = [t["state"] for t in task_instances]
    all_success = bool(states) and all(s == "success" for s in states)
    in_window = datetime.fromisoformat(run["run_after"]) >= EVIDENCE_START
    return {
        **run,
        "run_type": run["run_id"].split("__")[0],
        "task_instances": task_instances,
        "state_counts": dict(Counter(str(s) for s in states)),
        "all_success": all_success,
        "counts_as_evidence": all_success and in_window and ran_on_snowflake(manifest),
    }


def dag_runs(cli, dag_id):
    try:
        return airflow_json(cli, "dags", "list-runs", dag_id)
    except subprocess.CalledProcessError as exc:
        if "does not exist" in exc.stdout + exc.stderr:
            return []  # Not serialized into this metadata database yet, so it has no runs.
        raise


def collect(cli=RUN_SH, *, recent=None):
    """All runs per DAG, newest first; recent limits how many get the per-run CLI call."""
    report = {}
    for dag_id in DAG_IDS:
        runs = dag_runs(cli, dag_id)
        runs.sort(key=lambda r: str(r.get("run_after")), reverse=True)
        report[dag_id] = [
            summarize_run(
                run,
                airflow_json(cli, "tasks", "states-for-dag-run", dag_id, run["run_id"]),
                run_manifest(dag_id, run["run_id"]),
            )
            for run in runs[:recent]
        ]
    return report


def export(cli=RUN_SH, output=OUTPUT):
    runs = collect(cli)
    flat = [run for dag_runs in runs.values() for run in dag_runs]
    document = {
        "exported_at": datetime.now(UTC).isoformat(),
        "rule": EVIDENCE_RULE,
        "run_count": len(flat),
        "all_success_count": sum(run["all_success"] for run in flat),
        "evidence_count": sum(run["counts_as_evidence"] for run in flat),
        "runs": runs,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def next_execution(cli, dag_id):
    try:
        lines = airflow_lines(cli, "dags", "next-execution", dag_id)
    except subprocess.CalledProcessError:
        return "unknown (DAG not in the metadata database yet)"
    return lines[-1].strip() if lines else "unknown"


def last_alerts(limit=5):
    return ALERT_LOG.read_text().splitlines()[-limit:] if ALERT_LOG.exists() else []


def status_text(cli=RUN_SH):
    lines = [f"SponsorAtlas orchestration status, {datetime.now(UTC).isoformat()}", ""]
    lines.append("Recent runs (all-success counts every task instance; a skipped run is no):")
    for dag_id, runs in collect(cli, recent=5).items():
        lines.append(f"  {dag_id}:")
        for run in runs:
            verdict = "yes" if run["all_success"] else "no"
            evidence = "yes" if run["counts_as_evidence"] else "no"
            lines.append(
                f"    {run['run_id']}  state={run['state']}  all-success={verdict}  "
                f"evidence={evidence}  {run['state_counts']}"
            )
        if not runs:
            lines.append("    none")
    rows = guard.read_ledger()
    unclosed = sum(row["status"] == "open" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    lines += [
        "",
        f"Ledger total (ESTIMATE, not metered): {guard.ledger_total():.4f} credits, "
        f"{len(rows)} runs, {unclosed} not closed, {failed} failed, "
        f"limit {guard.LEDGER_LIMIT_CREDITS}",
    ]
    state = guard.read_state()
    metered = state.get("last_metered")
    baseline = state.get("metered_baseline")
    if baseline:
        lines.append(
            f"Metered baseline: {baseline['credits_used_total']:.4f} credits, "
            f"read {baseline['recorded_at']}"
        )
    if metered:
        lines.append(
            f"Last metered total: {metered['credits_used_total']:.4f} credits, read "
            f"{metered['recorded_at']}, limit {guard.ACCOUNT_LIMIT_CREDITS}. {metered.get('lag_note')}"
        )
    else:
        lines.append("Last metered total: none recorded")
    reason = guard.stop_reason()
    lines.append(f"STOP: present. {reason}" if reason else "STOP: absent")
    alerts = last_alerts()
    lines += ["", "Last alerts:", *(f"  {a}" for a in alerts or ["none"]), ""]
    lines.append("Next scheduled run:")
    lines += [f"  {dag_id}: {next_execution(cli, dag_id)}" for dag_id in DAG_IDS]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="print plain-text status only")
    parser.add_argument("--cli", default=RUN_SH, type=Path, help="run.sh-style Airflow wrapper")
    parser.add_argument("--output", default=OUTPUT, type=Path)
    args = parser.parse_args()
    if args.status:
        print(status_text(args.cli))
        return
    document = export(args.cli, args.output)
    print(
        f"{document['run_count']} runs, {document['all_success_count']} all-success, "
        f"{document['evidence_count']} count as evidence"
    )
    print(args.output)


if __name__ == "__main__":
    main()
