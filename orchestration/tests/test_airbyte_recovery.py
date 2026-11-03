"""Review regressions at the real urllib wrapper boundary; no network or cloud."""

import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from urllib.error import URLError

import pytest

from orchestration.sponsoratlas_orchestration import runtime, trial


@pytest.fixture
def transport(monkeypatch, tmp_path):
    for key, value in {
        "SPONSORATLAS_ORCHESTRATION_MODE": "synthetic_http",
        "SPONSORATLAS_ALERT_TRANSPORT": "local_http",
        "SPONSORATLAS_ALERT_URL": "http://127.0.0.1/alerts",
        "DBT_TARGET": "local",
        "SEMANTIC_SOURCE": "parquet",
        "AIRBYTE_API_URL": "http://127.0.0.1/api",
        "AIRBYTE_CONNECTION_ID": "connection",
        "AIRBYTE_CLIENT_ID": "synthetic",
        "AIRBYTE_CLIENT_SECRET": "synthetic",
        "SPONSORATLAS_AIRBYTE_STATE_DIR": str(tmp_path / "state"),
    }.items():
        monkeypatch.setenv(key, value)
    budget = {"left": 1000}

    def remaining(minimum=0):
        if budget["left"] <= minimum:
            raise RuntimeError("Insufficient trial window")
        return budget["left"]

    monkeypatch.setattr(trial, "trial_remaining", remaining)
    calls = []

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def respond(data):
        return Response(json.dumps(data).encode())

    return calls, budget, respond, tmp_path / "state"


@pytest.mark.parametrize("failure", [TimeoutError("secret URL"), URLError(TimeoutError("secret"))])
@pytest.mark.parametrize("at_token", [False, True])
def test_real_transport_timeout_cancels_known_job_and_retry_never_posts(
    transport, monkeypatch, failure, at_token
):
    calls, budget, respond, directory = transport
    injected = False

    def open_(req, *, timeout):
        nonlocal injected
        calls.append((req.method, req.full_url, timeout))
        if req.full_url.endswith("/applications/token"):
            if at_token and not injected and any(url.endswith("/jobs") for _, url, _ in calls):
                injected = True
                budget["left"] = 950
                raise failure
            return respond({"access_token": "synthetic"})
        if req.full_url.endswith("/jobs"):
            return respond({"jobId": 42})
        if req.method == "DELETE":
            return respond({"status": "cancelled"})
        if any(method == "DELETE" for method, _, _ in calls):
            return respond({"status": "cancelled"})
        budget["left"] = 950
        raise failure

    monkeypatch.setattr(runtime, "urlopen", open_)
    with pytest.raises(TimeoutError, match="Service transport failed"):
        runtime.airbyte_sync(run_id="timeout")
    assert sum(method == "DELETE" for method, _, _ in calls) == 1
    old = len(calls)
    with pytest.raises(RuntimeError, match="terminal failure"):
        runtime.airbyte_sync(run_id="timeout")
    assert len(calls) == old
    assert sum(method == "POST" and url.endswith("/jobs") for method, url, _ in calls) == 1
    assert any(json.loads(p.read_text())["status"] == "cancelled" for p in directory.glob("*.json"))


def test_existing_job_near_cutoff_recovers_cleanup_without_new_admission(transport, monkeypatch):
    calls, budget, respond, directory = transport
    directory.mkdir()
    key = hashlib.sha256(b"http://127.0.0.1/api\0connection\0previous").hexdigest()
    (directory / (key + ".json")).write_text(
        json.dumps(
            {
                "status": "submitted",
                "job_id": "42",
                "started_at": datetime.now(UTC).isoformat(),
                "run_id": "previous",
                "connection_id": "connection",
                "mode": "synthetic_http",
            }
        )
    )
    budget["left"] = 100

    def open_(req, *, timeout):
        calls.append((req.method, req.full_url, timeout))
        if req.full_url.endswith("/applications/token"):
            return respond({"access_token": "synthetic"})
        return respond({"status": "cancelled"})

    monkeypatch.setattr(runtime, "urlopen", open_)
    with pytest.raises(TimeoutError):
        runtime.airbyte_sync(run_id="previous")
    assert any(method == "DELETE" for method, _, _ in calls)
    assert not any(url.endswith("/jobs") for _, url, _ in calls)
    assert all(0 < timeout <= 15 for _, _, timeout in calls)


@pytest.mark.parametrize("ambiguous", [True, False])
def test_unresolved_previous_run_blocks_next_run_connection(transport, monkeypatch, ambiguous):
    calls, budget, respond, _ = transport

    def open_(req, *, timeout):
        calls.append((req.method, req.full_url, timeout))
        if req.full_url.endswith("/alerts"):
            return respond({"received": True})
        if req.full_url.endswith("/applications/token"):
            return respond({"access_token": "synthetic"})
        if req.full_url.endswith("/jobs"):
            if ambiguous:
                raise TimeoutError("POST accepted, response lost")
            return respond({"jobId": 42})
        budget["left"] = 950
        raise TimeoutError("persistent transport failure")

    monkeypatch.setattr(runtime, "urlopen", open_)
    with pytest.raises(RuntimeError):
        runtime.airbyte_sync(run_id="first")
    count = len(calls)
    with pytest.raises(RuntimeError, match="connection.*unresolved"):
        runtime.airbyte_sync(run_id="next")
    assert len(calls) == count
    assert sum(method == "POST" and url.endswith("/jobs") for method, url, _ in calls) == 1


def test_existing_job_after_trial_cutoff_records_uncertain_without_dispatch(transport, monkeypatch):
    calls, budget, _, directory = transport
    directory.mkdir()
    key = hashlib.sha256(b"http://127.0.0.1/api\0connection\0expired").hexdigest()
    (directory / (key + ".json")).write_text(
        json.dumps(
            {
                "status": "submitted",
                "job_id": "42",
                "started_at": datetime.now(UTC).isoformat(),
                "run_id": "expired",
                "connection_id": "connection",
                "mode": "synthetic_http",
            }
        )
    )
    budget["left"] = -1
    monkeypatch.setattr(
        runtime, "urlopen", lambda *_a, **_k: pytest.fail("No cloud dispatch past cutoff")
    )
    monkeypatch.setattr(runtime, "notify", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="cancellation unconfirmed"):
        runtime.airbyte_sync(run_id="expired")
    assert calls == []
    assert any(
        json.loads(p.read_text())["status"] == "cancel_unconfirmed"
        for p in directory.glob("*.json")
    )


def test_initial_state_commits_finish_before_first_transport_and_failure_blocks_new_run(
    transport, monkeypatch
):
    from orchestration.sponsoratlas_orchestration import airbyte_state

    calls, _, respond, directory = transport
    original = airbyte_state.atomic_json
    commits = []

    def commit(path, value):
        original(path, value)
        commits.append((path.name, value["status"]))

    def open_(req, *, timeout):
        calls.append((req.method, req.full_url, timeout))
        assert len(commits) >= 2
        assert commits[0][1] == commits[1][1] == "trigger_pending"
        assert len(list(directory.glob("*.json"))) == 2
        if req.full_url.endswith("/applications/token"):
            return respond({"access_token": "synthetic"})
        if req.full_url.endswith("/jobs"):
            return respond({"jobId": 42})
        return respond({"status": "succeeded"})

    monkeypatch.setattr(airbyte_state, "atomic_json", commit)
    monkeypatch.setattr(runtime, "urlopen", open_)
    runtime.airbyte_sync(run_id="first")
    assert len(commits) == 6  # initial, submitted and confirmed terminal, both records each.

    def fail_audit(path, value):
        if not path.name.startswith("connection-"):
            raise OSError("audit disk failure after durable connection marker")
        original(path, value)

    monkeypatch.setattr(airbyte_state, "atomic_json", fail_audit)
    count = len(calls)
    with pytest.raises(OSError, match="audit disk failure"):
        runtime.airbyte_sync(run_id="second")
    with pytest.raises(RuntimeError, match="connection.*unresolved"):
        runtime.airbyte_sync(run_id="third")
    assert len(calls) == count


@pytest.mark.parametrize("status", ["trigger_pending", "cancel_unconfirmed"])
def test_legacy_unresolved_previous_run_is_indexed_and_blocks_new_post(
    transport, monkeypatch, status
):
    _, _, _, directory = transport
    directory.mkdir()
    key = hashlib.sha256(b"http://127.0.0.1/api\0connection\0legacy").hexdigest()
    old = {"run_id": "legacy", "status": status, "connection_id": "connection"}
    if status == "cancel_unconfirmed":
        old["job_id"] = "42"
    (directory / (key + ".json")).write_text(json.dumps(old))
    monkeypatch.setattr(
        runtime, "urlopen", lambda *_a, **_k: pytest.fail("Unresolved legacy job must block POST")
    )
    with pytest.raises(RuntimeError, match="connection.*unresolved"):
        runtime.airbyte_sync(run_id="next")
    assert json.loads(next(directory.glob("connection-*.json")).read_text()) == old


def test_cancel_confirmation_after_absolute_cutoff_remains_unresolved(transport, monkeypatch):
    calls, budget, respond, directory = transport

    def open_(req, *, timeout):
        calls.append((req.method, req.full_url, timeout))
        if req.full_url.endswith("/alerts"):
            return respond({"received": True})
        if req.full_url.endswith("/applications/token"):
            return respond({"access_token": "synthetic"})
        if req.full_url.endswith("/jobs"):
            return respond({"jobId": 42})
        if req.method == "DELETE":
            return respond({"status": "cancelled"})
        if any(method == "DELETE" for method, _, _ in calls):
            budget["left"] = -1
            return respond({"status": "cancelled"})
        raise TimeoutError("GET timeout")

    monkeypatch.setattr(runtime, "urlopen", open_)
    with pytest.raises(RuntimeError, match="cancellation unconfirmed"):
        runtime.airbyte_sync(run_id="late-cancel")
    assert (
        json.loads(next(directory.glob("connection-*.json")).read_text())["status"]
        == "cancel_unconfirmed"
    )


@pytest.mark.parametrize("pause_at_ancestor", [False, True])
def test_runtime_observer_dispatch_has_own_parent_barriers_if_creator_stops(
    transport, monkeypatch, pause_at_ancestor
):
    from pathlib import Path

    from orchestration.sponsoratlas_orchestration import airbyte_state as state

    calls, _, respond, old_directory = transport
    parent, directory = old_directory.parent / "new", old_directory.parent / "new" / "state"
    monkeypatch.setenv("SPONSORATLAS_AIRBYTE_STATE_DIR", str(directory))
    pause_at = parent if pause_at_ancestor else directory
    original_mkdir, original_sync = Path.mkdir, state.sync_directory
    events, at_post = [], []
    worker = {"name": "A", "paused": False}

    class CreatorStopped(Exception):
        pass

    def sync(path):
        original_sync(path)
        events.append((worker["name"], str(path)))

    def open_(req, *, timeout):
        calls.append((req.method, req.full_url, timeout))
        if req.full_url.endswith("/applications/token"):
            return respond({"access_token": "synthetic"})
        if req.full_url.endswith("/jobs"):
            at_post.extend(events)
            return respond({"jobId": 42})
        return respond({"status": "succeeded"})

    def mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        if path == pause_at and not worker["paused"]:
            worker.update(name="B", paused=True)
            assert runtime.airbyte_sync(run_id="observer-B")["airbyte_job_id"] == "42"
            raise CreatorStopped  # A never reaches its pending parent fsync.

    monkeypatch.setattr(state.Path, "mkdir", mkdir)
    monkeypatch.setattr(state, "sync_directory", sync)
    monkeypatch.setattr(runtime, "urlopen", open_)
    with pytest.raises(CreatorStopped):
        state.ensure_directory(directory)
    assert sum(url.endswith("/jobs") for _, url, _ in calls) == 1
    for needed in [directory, parent, old_directory.parent]:
        assert ("B", str(needed)) in at_post, (
            "Real runtime POST crossed an unsynced directory-chain entry: " + str(at_post)
        )


def test_parent_directory_barrier_failure_refuses_first_dispatch(transport, monkeypatch):
    from orchestration.sponsoratlas_orchestration import airbyte_state as state

    calls, _, _, directory = transport
    directory.mkdir()  # Visible directory supplied by another creator is not a barrier.
    original_sync = state.sync_directory

    def sync(path):
        if path == directory.parent:
            raise OSError("parent directory fsync failed")
        original_sync(path)

    monkeypatch.setattr(state, "sync_directory", sync)
    monkeypatch.setattr(
        runtime, "urlopen", lambda *_a, **_k: pytest.fail("Unsynced parent must refuse dispatch")
    )
    with pytest.raises(OSError, match="parent directory fsync failed"):
        runtime.airbyte_sync(run_id="barrier-failed")
    assert calls == []
    assert not list(directory.glob("*.json"))
