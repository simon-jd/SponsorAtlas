"""Runtime boundaries: board loads, bounded Airbyte jobs, native freshness, configured alerts.

No service calls at module import. State must be on a durable shared volume for
production workers; an ambiguous POST is never silently repeated on retry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import subprocess
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = ROOT / ".tools/orchestration/artifacts"
ALERT_LOG = ROOT / ".tools/orchestration/alerts/alerts.jsonl"
OBSERVATION_SOURCE = "source:facts_input.job_observations"
OBSERVATION_SOURCE_ID = "source.sponsoratlas.facts_input.job_observations"
# Terraform has no SNAPSHOTS schema, so the Snowflake snapshot lives in INTERMEDIATE (dbt/README.md).
DBT_VARS = {"enable_private_applications": False, "snapshot_schema": "INTERMEDIATE"}
_DBT_ARTIFACTS = (
    str(ARTIFACTS_ROOT)
    + "/{{ run_id | replace('/', '_') | replace('..', '_') }}/{{ task.task_id }}"
)
DBT_ENV = {"DBT_TARGET_PATH": _DBT_ARTIFACTS + "/target", "DBT_LOG_PATH": _DBT_ARTIFACTS + "/logs"}
JOB_GATES = ["facts_grain", "facts_job_observation_accounting", "facts_job_fields_present"]
QUALITY_GATES = [
    "facts_grain",
    "facts_job_observation_accounting",
    "facts_job_fields_present",
    "facts_accounting",
    "facts_bls_conflicts",
    "facts_bls_headers",
    "dimension_mapping_accounting",
    "dimension_reference_integrity",
    "scd2_no_overlap",
    "staging_quality",
    "unmapped_columns",
    "mapping_integrity",
    "file_provenance",
    "board_provenance",
    "cdc_quality",
]


def safe_run_id(run_id):
    """A file name for an Airflow run id, which can hold ':' and '+'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip(".") or "_"


def run_dir(run_id):
    return ARTIFACTS_ROOT / safe_run_id(run_id)


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required configuration missing: {name}")
    return value


def checked_url(value, *, local_only=False):
    url = urlsplit(value)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if url.username or url.password or url.fragment or url.query or not url.netloc:
        raise ValueError("Service URL must have no credentials, query, or fragment")
    if (local_only and not (local and url.scheme == "http")) or (
        not local_only and url.scheme != "https"
    ):
        raise ValueError(
            "Production services require HTTPS; synthetic transport requires loopback HTTP"
        )
    return value.rstrip("/")


def request_json(url, *, payload=None, token=None, timeout=10, expect_json=True, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method or ("POST" if payload is not None else "GET"),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise RuntimeError("Service response exceeds 1 MiB cap")
            return (json.loads(data) if data else {}) if expect_json else data.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 -- transport boundary redacts webhook secrets
        # urllib HTTPError includes URLs, which may contain a Slack webhook secret.
        if isinstance(exc, TimeoutError) or (
            isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
        ):
            raise TimeoutError("Service transport failed (TimeoutError)") from None
        raise RuntimeError(f"Service transport failed ({type(exc).__name__})") from None


def notification_settings():
    kind = required("SPONSORATLAS_ALERT_TRANSPORT")
    if kind in {"slack", "local_http"}:
        if (
            kind == "local_http"
            and os.environ.get("SPONSORATLAS_ORCHESTRATION_MODE") != "synthetic_http"
        ):
            raise ValueError("local_http alerts require explicit synthetic_http mode")
        checked_url(required("SPONSORATLAS_ALERT_URL"), local_only=kind == "local_http")
    elif kind == "local_log":
        pass  # Explicit opt-in: nothing is delivered, and every line says so.
    elif kind == "smtp":
        for key in [
            "SPONSORATLAS_SMTP_HOST",
            "SPONSORATLAS_SMTP_USER",
            "SPONSORATLAS_SMTP_PASSWORD",
            "SPONSORATLAS_ALERT_FROM",
            "SPONSORATLAS_ALERT_TO",
        ]:
            required(key)
    else:
        raise ValueError("Alert transport must be slack, smtp, local_log, or local_http")
    return kind


def append_local_alert(payload):
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = {
        **payload,
        "at": datetime.now(UTC).isoformat(),
        "delivery": "LOCAL_LOG_NOT_DELIVERED",
    }
    fd = os.open(ALERT_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(line, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    directory = os.open(ALERT_LOG.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def notify(context=None, *, event="task_failure", detail=None):
    context = context or {}
    run = context.get("dag_run") or {}
    ti = context.get("task_instance")

    def field(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    payload = {
        "event": event,
        "dag_id": field(run, "dag_id") or context.get("dag_id"),
        "run_id": field(run, "run_id") or field(run, "dag_run_id") or context.get("run_id"),
        "task_id": field(ti, "task_id"),
    }
    if detail:
        payload["detail"] = detail
    text = "SponsorAtlas " + json.dumps(payload, sort_keys=True)
    kind = notification_settings()
    if kind == "local_log":
        append_local_alert(payload)
    elif kind == "smtp":
        message = EmailMessage()
        message["From"] = required("SPONSORATLAS_ALERT_FROM")
        message["To"] = required("SPONSORATLAS_ALERT_TO")
        message["Subject"] = "SponsorAtlas orchestration " + event
        message.set_content(text)
        with smtplib.SMTP_SSL(
            required("SPONSORATLAS_SMTP_HOST"),
            int(os.environ.get("SPONSORATLAS_SMTP_PORT", "465")),
            timeout=10,
        ) as smtp:
            smtp.login(required("SPONSORATLAS_SMTP_USER"), required("SPONSORATLAS_SMTP_PASSWORD"))
            smtp.send_message(message)
    else:
        response = request_json(
            required("SPONSORATLAS_ALERT_URL"),
            payload={"text": text} if kind == "slack" else payload,
            expect_json=kind != "slack",
        )
        if kind == "slack" and response.strip() != "ok":
            raise RuntimeError("Slack webhook did not acknowledge delivery")
    print(json.dumps({"notification_transport": kind, **payload}))


def deadline_alert(context=None):
    notify(context, event="runtime_deadline_exceeded")


def airbyte_sync(**context):
    notification_settings()  # missing routing must fail before an external sync is started
    mode = required("SPONSORATLAS_ORCHESTRATION_MODE")
    if mode not in {"production", "synthetic_http"}:
        raise ValueError("Explicit production or synthetic_http mode required")
    expected_target, expected_source = (
        ("snowflake", "snowflake") if mode == "production" else ("local", "parquet")
    )
    if required("DBT_TARGET") != expected_target or required("SEMANTIC_SOURCE") != expected_source:
        raise ValueError("Mode, dbt target, and Cube source must agree explicitly")
    url = checked_url(required("AIRBYTE_API_URL"), local_only=mode == "synthetic_http")
    from orchestration.sponsoratlas_orchestration.airbyte_state import AirbyteState
    from orchestration.sponsoratlas_orchestration.trial import trial_remaining

    connection = required("AIRBYTE_CONNECTION_ID")
    for name in ["AIRBYTE_CLIENT_ID", "AIRBYTE_CLIENT_SECRET"]:
        required(name)
    with AirbyteState(
        required("SPONSORATLAS_AIRBYTE_STATE_DIR"), url, connection, context["run_id"]
    ) as store:
        state = store.load()
        if state and state["status"] in {"failed", "cancelled", "incomplete"}:
            raise RuntimeError("Airbyte job terminal failure: " + state["status"])
        if state and state["status"] == "succeeded":
            return {"mode": mode, "airbyte_job_id": state["job_id"]}
        if state and state["status"] == "cancel_unconfirmed":
            raise RuntimeError(
                "Airbyte cancellation unresolved; reconcile durable job state before retry"
            )
        if state and not state.get("job_id"):
            raise RuntimeError(
                "Ambiguous Airbyte POST: reconcile durable state; refusing duplicate sync"
            )
        if state is None:
            trial_remaining(960)  # Admission applies ONLY to new work.
            state = {
                "status": "trigger_pending",
                "started_at": datetime.now(UTC).isoformat(),
                "run_id": context["run_id"],
                "connection_id": connection,
                "mode": mode,
            }
            store.commit(state)  # Both authority/audit are durable before token or POST.
        return _airbyte_job(context, mode, url, connection, store, state)


def _airbyte_job(context, mode, url, connection, store, state):
    from orchestration.sponsoratlas_orchestration.airbyte_state import TERMINAL
    from orchestration.sponsoratlas_orchestration.trial import trial_remaining

    started = datetime.fromisoformat(state["started_at"])

    def remaining(cap=30):
        try:
            trial_budget = trial_remaining(0) - 60
        except RuntimeError:
            # Admission already passed. A now-closed trial must enter the same-job
            # cancellation path rather than abandon a possibly running cloud job.
            raise TimeoutError("Airbyte trial dispatch window closed") from None
        seconds = min(840 - (datetime.now(UTC) - started).total_seconds(), trial_budget)
        if seconds <= 0:
            raise TimeoutError("Airbyte sync exceeded 900s total run budget")
        return min(cap, seconds)

    def call(path, payload=None):
        token = request_json(
            url + "/applications/token",
            payload={
                "client_id": required("AIRBYTE_CLIENT_ID"),
                "client_secret": required("AIRBYTE_CLIENT_SECRET"),
                "grant-type": "client_credentials",
            },
            timeout=remaining(),
        )["access_token"]
        result = request_json(url + path, payload=payload, token=token, timeout=remaining())
        if payload is None:
            remaining()  # Detect a transport returning after the absolute polling budget.
        return result

    try:
        if state.get("job_id"):
            # A recovered job needs cleanup authority, not a fresh 960s admission.
            try:
                trial_remaining(960)
            except RuntimeError:
                raise TimeoutError("Recovered Airbyte job requires near-cutoff cleanup") from None
        if not state.get("job_id"):
            job = call("/jobs", {"connectionId": connection, "jobType": "sync"})
            job_id = str(job["jobId"])
            if not job_id.isdecimal():
                raise RuntimeError("Airbyte returned invalid jobId; reconcile pending state")
            state.update(job_id=job_id, status="submitted")
            store.commit(state)
        while True:
            job = call("/jobs/" + state["job_id"])
            status = job["status"]
            if status in TERMINAL:
                state["status"] = status
                store.commit(state)
                if status != "succeeded":
                    raise RuntimeError("Airbyte job terminal failure: " + status)
                return {"mode": mode, "airbyte_job_id": state["job_id"]}
            if status not in {"pending", "running", "queued"}:
                raise RuntimeError("Unrecognized Airbyte job status")
            time.sleep(remaining(5))
    except (Exception, KeyboardInterrupt):
        if state["status"] in TERMINAL:
            raise
        if not state.get("job_id"):
            raise RuntimeError(
                "Airbyte submission timeout; reconcile pending state without another POST"
            ) from None

        # At most 60s from this failure, within the original total and trial cutoff.
        cleanup_deadline = time.monotonic() + 60

        def cleanup_remaining():
            left = min(
                900 - (datetime.now(UTC) - started).total_seconds(),
                trial_remaining(0),
                cleanup_deadline - time.monotonic(),
            )
            if left <= 0:
                raise TimeoutError("Airbyte cleanup window exhausted")
            return min(15, left)

        try:
            token = request_json(
                url + "/applications/token",
                payload={
                    "client_id": required("AIRBYTE_CLIENT_ID"),
                    "client_secret": required("AIRBYTE_CLIENT_SECRET"),
                    "grant-type": "client_credentials",
                },
                timeout=cleanup_remaining(),
            )["access_token"]
            request_json(
                url + "/jobs/" + state["job_id"],
                token=token,
                method="DELETE",
                timeout=cleanup_remaining(),
            )
            while True:
                status = request_json(
                    url + "/jobs/" + state["job_id"], token=token, timeout=cleanup_remaining()
                )["status"]
                cleanup_remaining()  # A late response cannot confirm within the deadline.
                if status in TERMINAL:
                    break
                time.sleep(min(2, cleanup_remaining()))
            state["status"] = status
        except Exception:  # noqa: BLE001 -- any unconfirmed cancellation requires manual reconciliation
            state["status"] = "cancel_unconfirmed"
        store.commit(state)
        if state["status"] == "cancel_unconfirmed":
            notify(context, event="airbyte_cancellation_unconfirmed")
            raise RuntimeError(
                "Airbyte cancellation unconfirmed; reconcile durable job state"
            ) from None
        raise


def run_date(context):
    """The run's UTC date; a manual run can have no logical date, so fall back to run_after."""
    value = context.get("logical_date") or context["dag_run"].run_after
    return value.astimezone(UTC).date().isoformat()


def load_boards(**context):
    """Load one day of board observations under .venv: the Airflow venv has no loader deps."""
    from orchestration.sponsoratlas_orchestration.trial import refuse_when_stopped, trial_remaining

    refuse_when_stopped()
    trial_remaining(960)
    manifest = run_dir(context["run_id"]) / "board-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "scripts.load_board_observations",
            "--date",
            run_date(context),
            "--output",
            str(manifest),
        ],
        cwd=ROOT,
        check=True,
        timeout=840,
    )
    return str(manifest)


def freshness(**context):
    target = required("DBT_TARGET")
    if target not in {"local", "snowflake"}:
        raise ValueError("Unsupported dbt target")
    command = [
        str(ROOT / "orchestration/bin/dbt"),
        "source",
        "freshness",
        "--project-dir",
        str(ROOT / "dbt"),
        "--profiles-dir",
        str(ROOT / "dbt"),
        "--target",
        target,
        "--select",
        OBSERVATION_SOURCE,
        "--vars",
        json.dumps(DBT_VARS),
    ]
    identity = hashlib.sha256(context["run_id"].encode()).hexdigest()
    artifacts = ARTIFACTS_ROOT / identity / "observations_freshness"
    env = {
        **os.environ,
        "DBT_TARGET_PATH": str(artifacts / "target"),
        "DBT_LOG_PATH": str(artifacts / "logs"),
        "SPONSORATLAS_DBT_PROCESS_TIMEOUT": "150",
    }
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=180)
    # dbt exits 0 with "Nothing to do" when the source declares no freshness: never a pass.
    results = json.loads((artifacts / "target/sources.json").read_text())["results"]
    status = {r["unique_id"]: r["status"] for r in results}.get(OBSERVATION_SOURCE_ID)
    if status != "pass":
        raise RuntimeError(f"{OBSERVATION_SOURCE} was not proven fresh (dbt status: {status})")


def clear_dead_refresh_owner(work):
    """A killed refresh leaves semantic/maintenance.cjs's lock behind, and nothing else clears it.

    Remove it only when it names a refresh whose process is gone. Anything else is a real owner,
    or unknown, and is refused here with a message the suppressed Node error cannot give.
    """
    lock = Path(work) / "work-owner.json"
    try:
        owner = json.loads(lock.read_text())
    except FileNotFoundError:
        return
    except ValueError:
        raise RuntimeError(f"Unreadable Cube work lock {lock}; reconcile it by hand") from None
    kind, pid = owner.get("kind"), owner.get("pid")
    if kind != "refresh" or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError(f"Cube work {lock} is owned by {kind} pid {pid}; stop that owner first")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        lock.unlink()
        print(json.dumps({"cube_lock": "removed_dead_refresh_owner", "pid": pid}))
    except PermissionError:
        raise RuntimeError(f"Cube work {lock} is owned by live refresh pid {pid}") from None
    else:
        raise RuntimeError(f"Cube work {lock} is owned by live refresh pid {pid}")


def cube_refresh(**context):
    source = required("SEMANTIC_SOURCE")
    if source == "parquet":
        if required("SPONSORATLAS_ORCHESTRATION_MODE") != "synthetic_http":
            raise ValueError("Production orchestration requires explicit live Snowflake refresh")
        # Local integration source is the separate dbt run DB; never an existing Task6 artifact.
        from orchestration.sponsoratlas_orchestration.snapshot import export_local

        work = export_local(context["run_id"])
    elif source == "snowflake":
        if required("SPONSORATLAS_ORCHESTRATION_MODE") != "production":
            raise ValueError("Snowflake refresh requires production mode")
        work = Path(required("SEMANTIC_WORK")).resolve()
    else:
        raise ValueError("Explicit parquet or snowflake Cube source required")
    env = {**os.environ, "SEMANTIC_WORK": str(work)}
    if source == "snowflake":
        from orchestration.sponsoratlas_orchestration.trial import refuse_when_stopped, trial_remaining

        refuse_when_stopped()
        trial_remaining(360)
        env["SNOWFLAKE_PRIVATE_KEY_FILE"] = required("SPONSORATLAS_CUBE_PRIVATE_KEY_FILE")
        env["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"] = os.environ.get(
            "SPONSORATLAS_CUBE_PRIVATE_KEY_PASSPHRASE", ""
        )
    clear_dead_refresh_owner(work)
    subprocess.run(
        [
            str(ROOT / ".tools/node-runtime/node-v24.22.1-linux-x64/bin/node"),
            "--max-old-space-size=768",
            str(ROOT / "semantic/scripts/refresh.cjs"),
        ],
        cwd=ROOT / "semantic",
        env=env,
        check=True,
        timeout=300,
    )
    return str(work)
