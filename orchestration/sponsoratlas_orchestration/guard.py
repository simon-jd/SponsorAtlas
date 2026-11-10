"""Preflight guard and per-run accounting for the scheduled Snowflake DAGs.

Ledger credits are ESTIMATES from run wall time at 1 credit/hour, never Snowflake metering.
The metered total comes from scripts.snowflake_metering and lags by up to hours. A run the
guard skips opens no ledger row and writes no manifest, so it can never count as evidence.
RUN_MINUTES is each DAG's dagrun_timeout, which bounds a run and caps its estimate.
No service calls at import.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

from orchestration.sponsoratlas_orchestration.runtime import (
    ROOT,
    notification_settings,
    notify,
    run_dir,
    safe_run_id,
)
from orchestration.sponsoratlas_orchestration.trial import trial_seconds_left

WORK = ROOT / ".tools/orchestration"
STOP = WORK / "STOP"
LEDGER = WORK / "credit-ledger.jsonl"
STATE = WORK / "guard-state.json"
EVIDENCE = ROOT / "docs/evidence/task09-airflow-snowflake-20261106"
VENV_PYTHON = ROOT / ".venv/bin/python"

LEDGER_LIMIT_CREDITS = 2.5
ACCOUNT_LIMIT_CREDITS = 123.0
MIN_TRIAL_SECONDS = 3600
MAX_CONNECTION_FAILURES = 2
MAX_METERING_AGE_DAYS = 2
UNCLOSED_CREDITS = 900 / 3600
OVERHEAD_SECONDS = 60
RUN_MINUTES = {"sponsoratlas_daily": 30, "sponsoratlas": 45}
ESTIMATE_BASIS = (
    "ESTIMATE, not metered: (run wall seconds + 60) / 3600 at 1 credit/hour, capped at the DAG's "
    "run timeout; an open run is charged 15 minutes and a failed run at least that"
)
DIFF_PATHS = ["dbt", "er", "governance", "ingestion", "orchestration", "scripts", "semantic"]


class MeteringFailure(RuntimeError):
    pass


def _timestamp(now):
    return now.astimezone(UTC).isoformat()


def _atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def _locked():
    """Serialize ledger and state updates across the two DAGs."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER.with_name(LEDGER.name + ".lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_ledger():
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def ledger_total():
    return sum(row["estimated_credits"] for row in read_ledger())


def _write_ledger(rows):
    _atomic_write(LEDGER, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def max_run_credits(dag_id):
    return (RUN_MINUTES[dag_id] * 60 + OVERHEAD_SECONDS) / 3600


def _charge(row, now, status, floor=0):
    """Settle a row. The cap keeps a run repaired hours later from reading as hours of spend."""
    wall = (now - datetime.fromisoformat(row["started_at"])).total_seconds()
    credits = min((wall + OVERHEAD_SECONDS) / 3600, max_run_credits(row["dag_id"]))
    row.update(
        status=status,
        ended_at=_timestamp(now),
        wall_seconds=wall,
        estimated_credits=max(credits, floor),
    )


def open_run(dag_id, run_id, now):
    """Charge the run as unclosed until record_usage closes it. A retry keeps the first row."""
    with _locked():
        rows = read_ledger()
        for row in rows:
            if (row["dag_id"], row["run_id"]) == (dag_id, run_id):
                return row
        row = {
            "dag_id": dag_id,
            "run_id": run_id,
            "status": "open",
            "started_at": _timestamp(now),
            "ended_at": None,
            "wall_seconds": None,
            "estimated_credits": UNCLOSED_CREDITS,
            "basis": ESTIMATE_BASIS,
        }
        _write_ledger([*rows, row])
        return row


def close_run(dag_id, run_id, now):
    with _locked():
        rows = read_ledger()
        for row in rows:
            if (row["dag_id"], row["run_id"]) == (dag_id, run_id):
                _charge(row, now, "closed")
                _write_ledger(rows)
                return row
    raise RuntimeError(f"No ledger row to close for {dag_id} {run_id}")


def fail_run(dag_id, run_id, now):
    """Charge a failed run its wall time, never less than an open run. No row: nothing to do."""
    with _locked():
        rows = read_ledger()
        for row in rows:
            if (row["dag_id"], row["run_id"]) == (dag_id, run_id):
                _charge(row, now, "failed", floor=UNCLOSED_CREDITS)
                _write_ledger(rows)
                return row
    return None


def on_run_failure(context=None):
    """DAG failure callback, including dagrun_timeout. Airflow may pass only dag, run_id, reason."""
    context = context or {}
    dag_id, run_id = context["dag"].dag_id, context["run_id"]
    try:
        fail_run(dag_id, run_id, datetime.now(UTC))
    finally:
        notify({**context, "dag_id": dag_id}, event="run_failure", detail=context.get("reason"))


def read_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def stop_reason():
    """The STOP marker's message, or None when absent. An empty marker still stops."""
    if not STOP.exists():
        return None
    return STOP.read_text().strip() or "STOP marker present with no recorded reason"


def _skip(reason, detail):
    print(json.dumps({"guard": "SKIP", "reason": reason, "detail": detail}, sort_keys=True))
    return False


def _trip(context, reason, detail, now):
    """Write STOP, which says why, then alert. An existing marker and its reason are kept."""
    run = context["dag_run"]
    message = {
        "stopped_by": "guard",
        "reason": reason,
        "detail": detail,
        "dag_id": run.dag_id,
        "run_id": run.run_id,
        "tripped_at": _timestamp(now),
    }
    STOP.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(STOP, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        try:
            os.write(fd, (json.dumps(message, sort_keys=True) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
    try:
        notify(context, event="guard_stop", detail=f"{reason}: {detail}")
    except Exception as exc:  # noqa: BLE001 -- STOP is written; a lost alert must not mask it
        print(json.dumps({"guard": "ALERT_FAILED", "error_type": type(exc).__name__}))
    return _skip(reason, detail)


def read_metered():
    """Account metered total from scripts.snowflake_metering under .venv. Output is one JSON."""
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "scripts.snowflake_metering"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise MeteringFailure("metering timed out") from None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise MeteringFailure(f"metering exit {result.returncode}, unreadable output") from None
    if result.returncode != 0 or "credits_used_total" not in data:
        raise MeteringFailure(f"metering exit {result.returncode}: {data.get('error_type')}")
    data["credits_used_total"] = float(data["credits_used_total"])
    return data


def check_metered(metered, now):
    """A zero or undated total proves nothing, and neither does a view that stopped advancing.

    It can only be called stale once a run admitted here is old enough to show in the view: the
    account may have been idle before this deployment, and today's usage lags by hours.
    """
    latest = metered.get("latest_usage_date")
    if not metered["credits_used_total"] or not latest:
        raise MeteringFailure("metering returned an empty total")
    today = now.astimezone(UTC).date()
    oldest = today - timedelta(days=MAX_METERING_AGE_DAYS)
    ran = (datetime.fromisoformat(row["started_at"]).date() for row in read_ledger())
    if date.fromisoformat(str(latest)[:10]) < oldest and any(day <= oldest for day in ran):
        raise MeteringFailure(f"metering is stale: latest usage date {latest} on {today}")


def _note_connection_failure(now):
    """Count once per UTC date: retries, and two DAGs an hour apart, are one outage, not a trip."""
    day = now.astimezone(UTC).date().isoformat()
    with _locked():
        state = read_state()
        if state.get("last_failed_date") != day:
            state["connection_failures"] = state.get("connection_failures", 0) + 1
            state["last_failed_date"] = day
            _atomic_write(STATE, json.dumps(state, sort_keys=True))
        return state["connection_failures"]


def _note_metered(metered, now):
    with _locked():
        state = read_state()
        reading = {**metered, "recorded_at": _timestamp(now)}
        # The first read of this deployment, so the absolute limit can be audited against it.
        state.setdefault("metered_baseline", reading)
        state.update(connection_failures=0, last_failed_date=None, last_metered=reading)
        _atomic_write(STATE, json.dumps(state, sort_keys=True))


def preflight(*, now=None, **context):
    """Return True to run. False skips every downstream task without failing the run."""
    now = now or datetime.now(UTC)
    run = context["dag_run"]
    reason = stop_reason()
    if reason:
        return _skip("stop_marker", reason)
    notification_settings()  # Missing alert routing fails before any external work.
    left = trial_seconds_left(now=now)
    if left < MIN_TRIAL_SECONDS:
        return _skip("trial_window", f"{left:.0f}s of trial left, need {MIN_TRIAL_SECONDS}s")
    total = ledger_total()
    if total >= LEDGER_LIMIT_CREDITS:
        detail = f"ESTIMATED ledger total {total:.4f} credits >= {LEDGER_LIMIT_CREDITS}"
        return _trip(context, "ledger_limit", detail, now)
    if os.environ.get("SPONSORATLAS_ORCHESTRATION_MODE") == "production":
        try:
            metered = read_metered()
            check_metered(metered, now)
        except MeteringFailure as exc:
            failures = _note_connection_failure(now)
            if failures >= MAX_CONNECTION_FAILURES:
                detail = f"{failures} consecutive UTC dates of failed metering, last: {exc}"
                return _trip(context, "connection_failures", detail, now)
            # Fail, not skip: Airflow retries a transient fault and the failure alert fires.
            raise RuntimeError(f"Metering failed, failure {failures}: {exc}") from None
        _note_metered(metered, now)
        # The whole run must fit: it can spend up to its timeout after being admitted.
        reserve = max_run_credits(run.dag_id)
        if metered["credits_used_total"] + reserve >= ACCOUNT_LIMIT_CREDITS:
            detail = (
                f"metered account total {metered['credits_used_total']:.4f} credits "
                f"+ {reserve:.4f} for this run >= {ACCOUNT_LIMIT_CREDITS}; "
                f"{metered.get('lag_note', '')}"
            )
            return _trip(context, "account_limit", detail, now)
    open_run(run.dag_id, run.run_id, now)
    return True


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, check=True, timeout=60
    ).stdout


def _versions():
    code = "import sys;from importlib.metadata import version as v;print(sys.version.split()[0],v('dbt-core'))"
    output = subprocess.run(
        [str(VENV_PYTHON), "-c", code], capture_output=True, text=True, check=True, timeout=60
    ).stdout
    project_python, dbt = output.split()
    return {
        "airflow_python": sys.version.split()[0],
        "project_python": project_python,
        "dbt_core": dbt,
    }


def _board_totals(run_id):
    path = run_dir(run_id) / "board-manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text())
    return {key: manifest.get(key) for key in ("status", "mode", "target", "totals", "table_count")}


def _without_account(metered):
    """Run manifests are committed to the public repository, so they do not keep the account."""
    if not metered or not isinstance(metered.get("identity"), dict):
        return metered
    return {**metered, "identity": {**metered["identity"], "account": "redacted"}}


def record_usage(*, now=None, **context):
    """Close the ledger row and write the run's manifest. Runs only after every task succeeded."""
    now = now or datetime.now(UTC)
    run = context["dag_run"]
    row = close_run(run.dag_id, run.run_id, now)
    logical_date = run.logical_date
    run_type = run.run_type
    manifest = {
        "dag_id": run.dag_id,
        "run_id": run.run_id,
        "run_type": getattr(run_type, "value", run_type),
        "logical_date": logical_date.isoformat() if logical_date else None,
        "start": row["started_at"],
        "end": row["ended_at"],
        "ledger": {
            "estimated_credits": row["estimated_credits"],
            "wall_seconds": row["wall_seconds"],
            "basis": ESTIMATE_BASIS,
        },
        "orchestration_mode": os.environ.get("SPONSORATLAS_ORCHESTRATION_MODE"),
        "dbt_target": os.environ.get("DBT_TARGET"),
        # The value preflight read; ACCOUNT_USAGE cannot have caught up with this run yet.
        "metered_total": _without_account(read_state().get("last_metered")),
        "metered_baseline": _without_account(read_state().get("metered_baseline")),
        "board_manifest_totals": _board_totals(run.run_id),
        "git_head": _git("rev-parse", "HEAD").decode().strip(),
        "git_diff_paths": DIFF_PATHS,
        "git_diff_scope": "tracked files only; untracked files are not in this hash",
        "git_diff_sha256": hashlib.sha256(_git("diff", "HEAD", "--", *DIFF_PATHS)).hexdigest(),
        **_versions(),
    }
    path = EVIDENCE / "runs" / f"{safe_run_id(run.run_id)}.json"
    _atomic_write(path, json.dumps(manifest, indent=2, sort_keys=True) + "\n", mode=0o644)
    return str(path)
