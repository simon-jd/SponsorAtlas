"""Runtime boundaries: bounded Airbyte jobs, native freshness, and configured alerts.

No service calls at module import. State must be on a durable shared volume for
production workers; an ambiguous POST is never silently repeated on retry.
"""

from __future__ import annotations

import hashlib
import json
import os
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
PUBLIC_SOURCES = [
    "source:jobs.greenhouse",
    "source:jobs.lever",
    "source:jobs.ashby",
    "source:jobs.cdc_job_postings",
]
QUALITY_GATES = [
    "facts_grain",
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
        raise ValueError("Alert transport must be slack, smtp, or local_http")
    return kind


def notify(context=None, *, event="task_failure"):
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
    text = "SponsorAtlas " + json.dumps(payload, sort_keys=True)
    kind = notification_settings()
    if kind == "smtp":
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
        *PUBLIC_SOURCES,
    ]
    identity = hashlib.sha256(context["run_id"].encode()).hexdigest()
    artifacts = ROOT / ".tools/orchestration/artifacts" / identity / "jobs_source_freshness"
    env = {
        **os.environ,
        "DBT_TARGET_PATH": str(artifacts / "target"),
        "DBT_LOG_PATH": str(artifacts / "logs"),
        "SPONSORATLAS_DBT_PROCESS_TIMEOUT": "150",
    }
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=180)


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
        from orchestration.sponsoratlas_orchestration.trial import trial_remaining

        trial_remaining(360)
        env["SNOWFLAKE_PRIVATE_KEY_FILE"] = required("SPONSORATLAS_CUBE_PRIVATE_KEY_FILE")
        env["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"] = os.environ.get(
            "SPONSORATLAS_CUBE_PRIVATE_KEY_PASSPHRASE", ""
        )
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
