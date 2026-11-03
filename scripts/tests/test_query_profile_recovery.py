"""Task10 review I1-I4: behavioral regression probes, no external services."""

import argparse
import copy
import json

import pytest

from scripts import query_profile_report as p


def test_exact_generated_phase_tag_excludes_other_run_and_literal_backslash():
    tag = "SponsorAtlas_Task10_RUN_baseline"
    cube_tag = tag + "_cube"
    seen = []

    class Session:
        def run(self, sql, params=None):
            if "QUERY_HISTORY q" not in sql:
                return []
            seen.append(params[1])
            assert "q.query_tag=%s" in sql and " LIKE " not in sql
            # Model exact equality, rather than returning fixture rows unconditionally.
            fixtures = [(tag, "source"), (cube_tag, "cube"),
                        ("SponsorAtlas_Task10_OTHER_baseline", "source"),
                        (r"SponsorAtlas_Task10\_RUN_baseline", "source")]
            return [{"QUERY_ID": q, "ATTRIBUTION_QUERY_ID": q,
                     "CREDITS_ATTRIBUTED_COMPUTE": .25} for candidate, q in fixtures
                    if candidate == params[1] and q in params[4:]]

    result = p.reconcile_experiment(Session(), {
        "kind": "ACTUAL_SNOWFLAKE_SOURCE_REFRESH_EXPERIMENT",
        "schema": "ANALYTICS.TASK10_RUN", "samples": [
            {"phase": "baseline", "query_tag": tag, "query_ids": ["source"],
             "cube": {"query_tag": cube_tag, "query_ids": ["cube"]}}]},
        "2026-10-24T00:00:00Z", "2026-10-24T01:00:00Z")
    assert result["samples"][0]["credits"] == .25
    assert result["samples"][0]["cube"]["credits"] == .25
    assert seen == [tag, cube_tag]


class ClockConnection:
    session_id = 123

    def __init__(self, clock, delay):
        self.now, self.delay, self.timeouts = clock, delay, []

    def cursor(self):
        connection = self

        class Cursor:
            sfqid = "query-id"
            description = (("N",),)

            def execute(self, sql, params, timeout):
                connection.timeouts.append(timeout)
                connection.now[0] += connection.delay

            def fetchall(self):
                return [(1,)]

            def close(self):
                pass

        return Cursor()


def test_cleanup_has_own_deadline_rejects_late_result_and_stops_dispatch():
    now = [0.0]
    connection = ClockConnection(now, delay=3)
    session = p.BoundedSession(connection, seconds=2, cleanup_seconds=3,
                               trial_deadline=100, clock=lambda: now[0])
    now[0] = 2
    with pytest.raises(TimeoutError):
        session.run("cleanup", cleanup=True)
    assert connection.timeouts == [3]
    with pytest.raises(TimeoutError):
        session.run("second cleanup", cleanup=True)
    assert len(connection.timeouts) == 1


def test_trial_cutoff_shortens_timeout_and_also_limits_status_confirmation():
    now = [0.0]
    connection = ClockConnection(now, delay=3)
    session = p.BoundedSession(connection, seconds=20, cleanup_seconds=2,
                               trial_deadline=3, clock=lambda: now[0])
    with pytest.raises(TimeoutError):
        session.run("cleanup", cleanup=True)
    assert connection.timeouts == [3]
    with pytest.raises(TimeoutError):
        session.query_status("query-id", cleanup=True)


def test_lost_query_id_and_running_ddl_cannot_be_cleared_by_absent_schema():
    class Session:
        session_id = 123
        query_ids = ()

        def __init__(self):
            self.calls = []

        def run(self, sql, params=None, cleanup=False):
            self.calls.append(sql)
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": p.WAREHOUSE, "SIZE": "X-Small", "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1}]
            return []  # even SHOW currently says absent

        def query_status(self, query_id, cleanup=False):
            return "RUNNING"

    for query_id in (None, "running-id"):
        report = {"mutation_protocol": 1, "originating_session": 123, "mutations": [
            {"intent_id": "intent", "originating_session": 123, "query_id": query_id,
             "statement": "CREATE SCHEMA ANALYTICS.TASK10_TEST", "state": "UNKNOWN"}]}
        session = Session()
        result = p.restore_experiment(session, "ANALYTICS.TASK10_TEST", "a" * 32,
                                      report=report, checkpoint=lambda r: None)
        assert result["status"] == "OPEN"
        assert report["mutations"][0]["state"] not in {"SUCCESS", "ABORTED"}
        assert not any(sql.startswith(("DROP", "ALTER WAREHOUSE")) for sql in session.calls)


def test_mutation_intent_and_origin_are_durable_before_dispatch_and_id_before_fetch():
    now = [0.0]
    snapshots = []
    report = {"mutation_protocol": 1, "originating_session": 123, "mutations": []}
    connection = ClockConnection(now, delay=0)
    original_cursor = connection.cursor

    def cursor():
        obj = original_cursor()
        execute = obj.execute

        def dispatch(*args, **kwargs):
            assert snapshots[-1]["mutations"][-1]["state"] == "INTENT"
            assert snapshots[-1]["mutations"][-1]["originating_session"] == 123
            return execute(*args, **kwargs)

        def fetch():
            assert snapshots[-1]["mutations"][-1]["query_id"] == "query-id"
            raise ConnectionError("lost response after dispatch")

        obj.execute, obj.fetchall = dispatch, fetch
        return obj

    connection.cursor = cursor
    base = p.BoundedSession(connection, clock=lambda: now[0])
    session = p.MutationSession(base, report, lambda r: snapshots.append(copy.deepcopy(r)))
    with pytest.raises(ConnectionError):
        session.run("CREATE SCHEMA ANALYTICS.TASK10_TEST")
    assert snapshots[-1]["mutations"][-1]["query_id"] == "query-id"
    assert snapshots[-1]["mutations"][-1]["state"] == "UNKNOWN"


def test_observer_syncs_full_chain_when_creator_pauses_after_mkdir(tmp_path, monkeypatch):
    target = tmp_path / "first" / "task10"
    creator_mkdir = p.Path.mkdir
    observer_barriers = []
    original_open, original_fsync = p.os.open, p.os.fsync
    handles = {}

    def record_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        handles[fd] = p.Path(path)
        return fd

    def record_sync(fd):
        observer_barriers.append(handles[fd])
        return original_fsync(fd)

    def mkdir(path, *args, **kwargs):
        result = creator_mkdir(path, *args, **kwargs)
        if path == target:
            # B observes A's visible mkdir before A has fsynced either new entry.
            p.durable_directory(target)
            assert {target, *target.parents} <= set(observer_barriers)
        return result

    monkeypatch.setattr(p.os, "open", record_open)
    monkeypatch.setattr(p.os, "fsync", record_sync)
    monkeypatch.setattr(p.Path, "mkdir", mkdir)
    p.durable_directory(target)


def test_existing_directory_barrier_failure_prevents_remote_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "ROOT", tmp_path)
    (tmp_path / ".tools/task10").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(p, "connect", lambda **kwargs: calls.append("remote"))
    monkeypatch.setattr(p.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync failed")))
    with pytest.raises(OSError, match="fsync failed"):
        p.remote_experiment(argparse.Namespace(mode="experiment"))
    assert calls == []
    assert not (tmp_path / ".tools/task10/remote-pending.json").exists()


def test_unknown_legacy_journal_cannot_recover_to_verified(tmp_path, monkeypatch):
    directory = tmp_path / ".tools/task10"
    directory.mkdir(parents=True)
    prior = {"schema": "ANALYTICS.TASK10_TEST", "owner_token": "a" * 32,
             "status": "RUNNING"}
    (directory / "remote-pending.json").write_text(json.dumps(prior))
    monkeypatch.setattr(p, "ROOT", tmp_path)
    result = p.restore_experiment(None, prior["schema"], prior["owner_token"],
                                  report=prior, checkpoint=lambda r: None)
    assert result["status"] == "OPEN"


def test_hard_deadline_interrupts_a_blocking_sdk_wait_before_it_returns():
    import time

    finished = []

    def blocking_sdk():
        time.sleep(.1)
        finished.append(True)

    with pytest.raises(TimeoutError, match="interrupted"):
        p.bounded_call(blocking_sdk, time.monotonic() + .01)
    assert finished == []


def test_terminal_confirmation_requires_status_after_cancel_acknowledgement():
    report = {"mutation_protocol": 1, "originating_session": 123, "mutations": [
        {"intent_id": "intent", "originating_session": 123, "query_id": "known-id",
         "statement": "ALTER WAREHOUSE SPONSORATLAS_XS SET WAREHOUSE_SIZE='SMALL'",
         "state": "UNKNOWN"}]}

    class Session:
        def __init__(self):
            self.statuses = iter(["RUNNING", "ABORTING", "ABORTED"])
            self.calls = []

        def query_status(self, query_id, cleanup=False):
            assert cleanup and query_id == "known-id"
            self.calls.append("status")
            return next(self.statuses)

        def run(self, sql, params=None, cleanup=False):
            assert cleanup and params == ("known-id",)
            assert sql == "SELECT SYSTEM$CANCEL_QUERY(%s)"
            self.calls.append("cancel-ack")
            return [{"RESULT": "cancel requested"}]

    session = Session()
    snapshots = []
    assert p.confirm_mutations(session, report, lambda r: snapshots.append(copy.deepcopy(r))) == []
    assert session.calls == ["status", "cancel-ack", "status", "status"]
    assert snapshots[0]["mutations"][0]["state"] == "UNKNOWN"
    assert snapshots[-1]["mutations"][0]["state"] == "ABORTED"


@pytest.mark.parametrize("tag", ["SponsorAtlas_Task10_OTHER_baseline",
                                  r"SponsorAtlas_Task10\_RUN_baseline"])
def test_other_run_or_literal_backslash_evidence_tag_refused(tag):
    class Session:
        def run(self, *args, **kwargs):
            pytest.fail("invalid phase evidence reached account usage")

    with pytest.raises(ValueError, match="exact phase"):
        p.reconcile_experiment(Session(), {
            "kind": "ACTUAL_SNOWFLAKE_SOURCE_REFRESH_EXPERIMENT",
            "schema": "ANALYTICS.TASK10_RUN", "samples": [
                {"phase": "baseline", "query_tag": tag, "query_ids": ["source"]}]},
            "2026-10-24T00:00:00Z", "2026-10-24T01:00:00Z")


@pytest.mark.parametrize("ancestor_index", [0, 1, -1])
def test_each_ancestor_fsync_failure_blocks_dispatch(tmp_path, monkeypatch, ancestor_index):
    directory = tmp_path / ".tools/task10"
    directory.mkdir(parents=True)
    chain = [directory, *directory.parents]
    failing = chain[ancestor_index]
    handles = {}
    original_open, original_fsync = p.os.open, p.os.fsync

    def record_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        handles[fd] = p.Path(path)
        return fd

    def sync(fd):
        if handles[fd] == failing:
            raise OSError("ancestor barrier failed")
        return original_fsync(fd)

    monkeypatch.setattr(p, "ROOT", tmp_path)
    monkeypatch.setattr(p.os, "open", record_open)
    monkeypatch.setattr(p.os, "fsync", sync)
    monkeypatch.setattr(p, "connect", lambda **kwargs: pytest.fail("barrier failure reached remote"))
    with pytest.raises(OSError, match="ancestor barrier"):
        p.remote_experiment(argparse.Namespace(mode="experiment"))


@pytest.mark.parametrize("state", ["legacy", "UNKNOWN"])
def test_verified_flag_cannot_release_unknown_or_legacy_mutation_journal(tmp_path, monkeypatch, state):
    directory = tmp_path / ".tools/task10"
    directory.mkdir(parents=True)
    report = {"restoration": {"status": "VERIFIED"}}
    if state != "legacy":
        report.update({"mutation_protocol": 1, "originating_session": 123, "mutations": [
            {"query_id": "not-terminal", "originating_session": 123, "state": state}]})
    (directory / "remote-pending.json").write_text(json.dumps(report))
    monkeypatch.setattr(p, "ROOT", tmp_path)
    monkeypatch.setattr(p, "connect", lambda **kwargs: pytest.fail("unproven VERIFIED flag reached remote"))
    with pytest.raises(RuntimeError, match="unresolved"):
        p.remote_experiment(argparse.Namespace(mode="experiment"))


def test_cleanup_ddl_late_response_stays_unknown_and_cannot_mark_restoration_verified():
    now = [0.0]
    connection = ClockConnection(now, delay=5)
    base = p.BoundedSession(connection, seconds=2, cleanup_seconds=3, clock=lambda: now[0])
    report = {"mutation_protocol": 1, "originating_session": 123, "mutations": []}
    session = p.MutationSession(base, report, lambda r: None)
    result = p.restore_experiment(session, "ANALYTICS.TASK10_TEST", "a" * 32,
                                  report=report, checkpoint=lambda r: None)
    assert result["status"] == "OPEN"
    assert report["mutations"][0]["state"] == "UNKNOWN"
    assert connection.timeouts == [5], "cutoff stops all subsequent restore SQL/status dispatch"


def test_known_terminal_ddl_recovers_before_show_and_cleanup():
    report = {"mutation_protocol": 1, "originating_session": 123, "mutations": [
        {"intent_id": "intent", "originating_session": 123, "query_id": "known-id",
         "statement": "CREATE SCHEMA ANALYTICS.TASK10_TEST", "state": "UNKNOWN"}]}

    class Session:
        session_id = 456  # recovery is a different, explicit current session

        def __init__(self):
            self.query_ids, self.trace = [], []

        def query_status(self, query_id, cleanup=False):
            assert cleanup and query_id == "known-id"
            self.trace.append("terminal-status")
            return "SUCCESS"

        def run(self, sql, params=None, cleanup=False):
            assert cleanup
            self.trace.append(sql)
            self.query_ids.append("recovery-" + str(len(self.trace)))
            if sql.startswith("SHOW WAREHOUSES"):
                return [{"NAME": p.WAREHOUSE, "SIZE": "X-Small", "AUTO_SUSPEND": 60,
                         "STATE": "SUSPENDED", "MIN_CLUSTER_COUNT": 1, "MAX_CLUSTER_COUNT": 1}]
            return []

    session = Session()
    result = p.restore_experiment(session, "ANALYTICS.TASK10_TEST", "a" * 32,
                                  report=report, checkpoint=lambda r: None)
    assert result["status"] == "VERIFIED"
    assert session.trace[0] == "terminal-status"
    assert report["mutations"][0]["originating_session"] == 123
    assert all(i["originating_session"] == 456 for i in report["mutations"][1:])
    report["restoration"] = result
    assert p.journal_cleared(report)


def test_timeout_is_recomputed_after_slow_cursor_creation():
    now = [0.0]
    connection = ClockConnection(now, delay=0)
    original = connection.cursor

    def cursor():
        now[0] += 4
        return original()

    connection.cursor = cursor
    session = p.BoundedSession(connection, seconds=2, cleanup_seconds=3, clock=lambda: now[0])
    assert session.run("cleanup", cleanup=True) == [{"N": 1}]
    assert connection.timeouts == [1]
