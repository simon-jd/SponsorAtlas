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
