"""Transport boundaries and retry state, using real loopback HTTP (not external platforms)."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError

import pytest

from orchestration.sponsoratlas_orchestration import runtime


@pytest.fixture
def sink(monkeypatch, tmp_path):
    events = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            events.append((self.path, body))
            response = {
                "/applications/token": {"access_token": "synthetic"},
                "/jobs": {"jobId": 42},
                "/alerts": {"received": True},
            }[self.path]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"succeeded"}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    for key, value in {
        "SPONSORATLAS_ORCHESTRATION_MODE": "synthetic_http",
        "SPONSORATLAS_ALERT_TRANSPORT": "local_http",
        "SPONSORATLAS_ALERT_URL": url + "/alerts",
        "DBT_TARGET": "local",
        "SEMANTIC_SOURCE": "parquet",
        "AIRBYTE_API_URL": url,
        "AIRBYTE_CONNECTION_ID": "synthetic-id",
        "AIRBYTE_CLIENT_ID": "synthetic",
        "AIRBYTE_CLIENT_SECRET": "synthetic",
        "SPONSORATLAS_AIRBYTE_STATE_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(key, value)
    yield events, tmp_path
    server.shutdown()
    server.server_close()
    thread.join()


def test_retry_reuses_existing_airbyte_job(sink):
    events, _ = sink
    assert runtime.airbyte_sync(run_id="one")["airbyte_job_id"] == "42"
    assert runtime.airbyte_sync(run_id="one")["airbyte_job_id"] == "42"
    assert sum(path == "/jobs" for path, _ in events) == 1
    assert runtime.airbyte_sync(run_id="two")["airbyte_job_id"] == "42"
    assert sum(path == "/jobs" for path, _ in events) == 2


def test_ambiguous_post_never_silently_duplicates(sink, monkeypatch):
    events, directory = sink
    original = runtime.request_json

    def uncertain(url, **kwargs):
        if url.endswith("/jobs"):
            original(url, **kwargs)
            raise TimeoutError("response lost after acceptance")
        return original(url, **kwargs)

    monkeypatch.setattr(runtime, "request_json", uncertain)
    with pytest.raises(RuntimeError, match="submission timeout"):
        runtime.airbyte_sync(run_id="ambiguous")
    monkeypatch.setattr(runtime, "request_json", original)
    with pytest.raises(RuntimeError, match="Ambiguous Airbyte POST"):
        runtime.airbyte_sync(run_id="ambiguous")
    assert sum(path == "/jobs" for path, _ in events) == 1
    assert next(directory.glob("*.json")).stat().st_mode & 0o777 == 0o600


def test_real_notification_transport(sink):
    events, _ = sink
    runtime.notify({"dag_run": {"dag_id": "sponsoratlas", "run_id": "fail-1"}})
    runtime.deadline_alert({"dag_run": {"dag_id": "sponsoratlas", "run_id": "slow-1"}})
    assert [(b["event"], b["run_id"]) for p, b in events if p == "/alerts"] == [
        ("task_failure", "fail-1"),
        ("runtime_deadline_exceeded", "slow-1"),
    ]


def test_production_never_uses_synthetic_fallback(sink, monkeypatch):
    events, _ = sink
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "production")
    with pytest.raises(ValueError, match="local_http"):
        runtime.airbyte_sync(run_id="forbidden")
    assert events == []


def test_local_log_alert_is_durable_private_and_marked_not_delivered(monkeypatch, tmp_path):
    log = tmp_path / "alerts/alerts.jsonl"
    monkeypatch.setattr(runtime, "ALERT_LOG", log)
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "production")
    monkeypatch.setenv("SPONSORATLAS_ALERT_TRANSPORT", "local_log")
    monkeypatch.delenv("SPONSORATLAS_ALERT_URL", raising=False)
    fsynced = []
    real_fsync = runtime.os.fsync
    monkeypatch.setattr(runtime.os, "fsync", lambda fd: (fsynced.append(fd), real_fsync(fd)))
    runtime.notify({"dag_run": {"dag_id": "d", "run_id": "one"}})
    runtime.notify({"dag_run": {"dag_id": "d", "run_id": "two"}}, event="guard_stop", detail="why")
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(x["run_id"], x["event"]) for x in lines] == [
        ("one", "task_failure"),
        ("two", "guard_stop"),
    ]
    assert lines[1]["detail"] == "why"
    assert {x["delivery"] for x in lines} == {"LOCAL_LOG_NOT_DELIVERED"}
    assert log.stat().st_mode & 0o777 == 0o600
    assert fsynced


def test_production_requires_an_explicit_alert_transport(monkeypatch):
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "production")
    monkeypatch.delenv("SPONSORATLAS_ALERT_TRANSPORT", raising=False)
    with pytest.raises(RuntimeError, match="SPONSORATLAS_ALERT_TRANSPORT"):
        runtime.notification_settings()
    monkeypatch.setenv("SPONSORATLAS_ALERT_TRANSPORT", "local_log")
    assert runtime.notification_settings() == "local_log"
    monkeypatch.setenv("SPONSORATLAS_ALERT_TRANSPORT", "local_http")
    monkeypatch.setenv("SPONSORATLAS_ALERT_URL", "http://127.0.0.1:1/alerts")
    with pytest.raises(ValueError, match="synthetic_http"):
        runtime.notification_settings()


def test_load_boards_runs_the_loader_under_project_venv(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    monkeypatch.setattr(runtime, "ARTIFACTS_ROOT", tmp_path)
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "synthetic_http")
    monkeypatch.setenv("DBT_TARGET", "local")
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, **_kw: calls.append(args))
    run_id = "scheduled__2026-11-06T08:00:00+00:00"
    logical = datetime(2026, 11, 6, 8, tzinfo=UTC)
    runtime.load_boards(run_id=run_id, logical_date=logical)
    manual = SimpleNamespace(run_after=datetime(2026, 11, 7, 1, tzinfo=UTC))
    runtime.load_boards(run_id="manual/../x", logical_date=None, dag_run=manual)
    assert calls[0][0] == str(runtime.ROOT / ".venv/bin/python")
    assert calls[0][1:5] == ["-m", "scripts.load_board_observations", "--date", "2026-11-06"]
    assert calls[0][5:] == [
        "--output",
        str(tmp_path / "scheduled__2026-11-06T08_00_00_00_00/board-manifest.json"),
    ]
    assert calls[1][4] == "2026-11-07"
    assert calls[1][6] == str(tmp_path / "manual_.._x/board-manifest.json")


def test_missing_target_fails_before_sync(sink, monkeypatch):
    events, _ = sink
    monkeypatch.delenv("SPONSORATLAS_ALERT_URL")
    with pytest.raises(RuntimeError, match="SPONSORATLAS_ALERT_URL"):
        runtime.airbyte_sync(run_id="missing")
    assert events == []


def test_no_secrets_in_transport_failure(monkeypatch):
    def failure(*_args, **_kwargs):
        raise HTTPError("https://secret.invalid/hook-secret", 500, "secret", {}, None)

    monkeypatch.setattr(runtime, "urlopen", failure)
    with pytest.raises(RuntimeError, match=r"^Service transport failed \(HTTPError\)$"):
        runtime.request_json("https://secret.invalid/hook-secret")


def test_slack_webhook_accepts_official_plain_ok(monkeypatch):
    from io import BytesIO

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(runtime, "urlopen", lambda *_args, **_kwargs: Response(b"ok"))
    monkeypatch.setenv("SPONSORATLAS_ALERT_TRANSPORT", "slack")
    monkeypatch.setenv("SPONSORATLAS_ALERT_URL", "https://hooks.slack.com/services/synthetic")
    runtime.notify({"dag_run": {"dag_id": "sponsoratlas", "run_id": "slack-protocol-only"}})


def test_airbyte_timeout_cancels_same_job_within_cleanup_reserve(sink, monkeypatch):
    from datetime import UTC, timedelta
    from datetime import datetime as DateTime

    clock = {"now": DateTime.now(UTC)}

    class Clock(DateTime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(runtime, "datetime", Clock)
    calls = []

    def request(url, **options):
        calls.append((url, options.get("method")))
        if url.endswith("/applications/token"):
            return {"access_token": "synthetic"}
        if url.endswith("/jobs"):
            return {"jobId": 42}
        if options.get("method") == "DELETE":
            return {"status": "cancelled"}
        if any(method == "DELETE" for _, method in calls):
            return {"status": "cancelled"}
        clock["now"] += timedelta(seconds=845)
        return {"status": "running"}

    monkeypatch.setattr(runtime, "request_json", request)
    with pytest.raises(TimeoutError):
        runtime.airbyte_sync(run_id="cancel-on-timeout")
    assert any(url.endswith("/jobs/42") and method == "DELETE" for url, method in calls)
    state = json.loads(next(sink[1].glob("*.json")).read_text())
    assert state["status"] == "cancelled"


def test_expired_production_never_dispatches_cloud(sink, monkeypatch):
    for key, value in {
        "SPONSORATLAS_ORCHESTRATION_MODE": "production",
        "DBT_TARGET": "snowflake",
        "SEMANTIC_SOURCE": "snowflake",
        "SPONSORATLAS_ALERT_TRANSPORT": "slack",
        "SPONSORATLAS_ALERT_URL": "https://hooks.slack.com/services/synthetic",
        "AIRBYTE_API_URL": "https://airbyte.example.invalid/v1",
        "SNOWFLAKE_TRIAL_STARTED_AT": "2026-09-01T00:00:00Z",
        "SNOWFLAKE_TRIAL_EXPIRES_AT": "2026-09-30T00:00:00Z",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="trial"):
        runtime.airbyte_sync(run_id="expired")
    assert sink[0] == []


def test_unconfirmed_cancel_is_durable_and_retry_cannot_trigger_new_sync(sink, monkeypatch):
    from datetime import UTC, timedelta
    from datetime import datetime as DateTime

    clock = {"now": DateTime.now(UTC)}
    calls = []

    class Clock(DateTime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(runtime, "datetime", Clock)
    original = runtime.request_json

    def request(url, **options):
        if url.endswith("/alerts"):
            return original(url, **options)
        calls.append((url, options.get("method")))
        if url.endswith("/applications/token"):
            return {"access_token": "synthetic"}
        if url.endswith("/jobs"):
            return {"jobId": 42}
        if options.get("method") == "DELETE":
            raise TimeoutError("cancellation transport lost")
        clock["now"] += timedelta(seconds=845)
        return {"status": "succeeded"}

    monkeypatch.setattr(runtime, "request_json", request)
    with pytest.raises(RuntimeError, match="cancellation unconfirmed"):
        runtime.airbyte_sync(run_id="cancel-unconfirmed")
    assert json.loads(next(sink[1].glob("*.json")).read_text())["status"] == "cancel_unconfirmed"
    with pytest.raises(RuntimeError, match="cancellation unresolved"):
        runtime.airbyte_sync(run_id="cancel-unconfirmed")
    assert sum(url.endswith("/jobs") for url, _ in calls) == 1
    assert any(
        body.get("event") == "airbyte_cancellation_unconfirmed"
        for path, body in sink[0]
        if path == "/alerts"
    )


def test_airbyte_near_trial_cutoff_enters_same_job_cleanup(sink, monkeypatch):
    from orchestration.sponsoratlas_orchestration import trial

    left = {"seconds": 1000}
    calls = []

    def remaining(minimum=0):
        if left["seconds"] <= minimum:
            raise RuntimeError("Insufficient trial window")
        return left["seconds"]

    def request(url, **options):
        calls.append((url, options.get("method")))
        if url.endswith("/applications/token"):
            return {"access_token": "synthetic"}
        if url.endswith("/jobs"):
            return {"jobId": 42}
        if options.get("method") == "DELETE":
            return {"status": "cancelled"}
        if any(method == "DELETE" for _, method in calls):
            return {"status": "cancelled"}
        left["seconds"] = 40
        return {"status": "running"}

    monkeypatch.setattr(trial, "trial_remaining", remaining)
    monkeypatch.setattr(runtime, "request_json", request)
    with pytest.raises(TimeoutError):
        runtime.airbyte_sync(run_id="trial-near-cutoff")
    assert any(url.endswith("/jobs/42") and method == "DELETE" for url, method in calls)
    assert json.loads(next(sink[1].glob("*.json")).read_text())["status"] == "cancelled"


def write_lock(work, **owner):
    work.mkdir(exist_ok=True)
    (work / "work-owner.json").write_text(json.dumps(owner))
    return work / "work-owner.json"


def test_a_dead_refresh_owner_is_removed_and_logged(tmp_path, capsys):
    import subprocess

    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = write_lock(tmp_path, kind="refresh", pid=dead.pid, nonce="n")
    runtime.clear_dead_refresh_owner(tmp_path)
    assert not lock.exists()
    assert "removed_dead_refresh_owner" in capsys.readouterr().out
    runtime.clear_dead_refresh_owner(tmp_path)  # No lock: nothing to do.


def test_a_live_owner_or_a_gateway_is_named_and_left_in_place(tmp_path):
    import os

    lock = write_lock(tmp_path, kind="refresh", pid=os.getpid(), nonce="n")
    with pytest.raises(RuntimeError, match=f"live refresh pid {os.getpid()}"):
        runtime.clear_dead_refresh_owner(tmp_path)
    assert lock.exists()
    lock = write_lock(tmp_path, kind="gateway", pid=4194999, nonce="n")
    with pytest.raises(RuntimeError, match="gateway pid"):
        runtime.clear_dead_refresh_owner(tmp_path)
    assert lock.exists()
    for bad in ('{"kind":"refresh","pid":0}', '{"kind":"refresh"}', "", "not json"):
        lock.write_text(bad)
        with pytest.raises(RuntimeError, match="Cube work"):
            runtime.clear_dead_refresh_owner(tmp_path)
        assert lock.read_text() == bad


def test_cube_refresh_retries_past_a_killed_owner_and_runs_once(monkeypatch, tmp_path):
    import subprocess

    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = write_lock(tmp_path, kind="refresh", pid=dead.pid, nonce="n")
    calls = []
    real_run = runtime.subprocess.run
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda args, **kw: calls.append((args, lock.exists())) if "refresh.cjs" in args[-1] else real_run(args, **kw),
    )
    monkeypatch.setenv("SPONSORATLAS_ORCHESTRATION_MODE", "production")
    monkeypatch.setenv("SEMANTIC_SOURCE", "snowflake")
    monkeypatch.setenv("SEMANTIC_WORK", str(tmp_path))
    monkeypatch.setenv("SPONSORATLAS_CUBE_PRIVATE_KEY_FILE", "/keys/CUBE_SVC.p8")
    monkeypatch.setenv("SNOWFLAKE_TRIAL_STARTED_AT", "2026-10-25T07:29:00Z")
    monkeypatch.setenv("SNOWFLAKE_TRIAL_EXPIRES_AT", "2026-11-24T00:00:00Z")
    assert runtime.cube_refresh(run_id="run-1") == str(tmp_path.resolve())
    assert len(calls) == 1 and calls[0][1] is False
