"""Probe control flow only; these tests do not assert real CDC acceptance."""

import pytest

from scripts.cdc_probe import main, probe


def test_probe_requires_insert_update_and_delete_for_both_tables():
    current = [""]
    mutations = []

    def mutate(phase):
        current[0] = phase
        mutations.append(phase)

    def fetch():
        row = (current[0], "deleted-at" if current[0] == "delete" else None)
        return [row, row]

    result = probe(mutate, fetch, lambda: {"job_id": 1})
    assert result["status"] == "PASS"
    assert mutations == ["insert", "update", "delete"]
    assert len(result["operations"]) == 3


def test_probe_fails_when_one_table_never_arrives():
    clock = [0]

    def wait(seconds):
        clock[0] += seconds

    with pytest.raises(TimeoutError):
        probe(
            lambda phase: None,
            lambda: [("insert", None), None],
            dict,
            monotonic=lambda: clock[0],
            wait=wait,
            budget=10,
        )


def test_dry_probe_never_connects():
    assert main([]) == 2


@pytest.mark.parametrize("late_call", ["mutate", "trigger", "fetch", "job_done"])
def test_probe_rejects_external_call_crossing_absolute_deadline(late_call):
    """In particular, a final successful job lookup at903s must never produce PASS."""
    clock = [0.0]
    current = [""]

    def mutate(phase):
        current[0] = phase
        if late_call == "mutate" and phase == "delete":
            clock[0] = 903.0

    def trigger():
        if late_call == "trigger" and current[0] == "delete":
            clock[0] = 903.0
        return {"job_id": 1}

    def fetch():
        if late_call == "fetch" and current[0] == "delete":
            clock[0] = 903.0
        row = (current[0], "tombstone" if current[0] == "delete" else None)
        return [row, row]

    def job_done(job):
        if late_call == "job_done" and current[0] == "delete":
            clock[0] = 903.0
        return True

    with pytest.raises(TimeoutError):
        probe(mutate, fetch, trigger, job_done=job_done, monotonic=lambda: clock[0])


def test_airbyte_token_and_job_requests_share_remaining_timeout(monkeypatch):
    import io

    from scripts.cdc_probe import AirbyteJobs, Deadline

    clock = [0.0]
    timeouts = []
    monkeypatch.setenv("AIRBYTE_API_URL", "http://localhost:8000/api/public/v1")
    monkeypatch.setenv("AIRBYTE_CLIENT_ID", "fixture-client")
    monkeypatch.setenv("AIRBYTE_CLIENT_SECRET", "fixture-secret")
    monkeypatch.setenv("AIRBYTE_CDC_CONNECTION_ID", "fixture-connection")

    def open_request(request, timeout):
        timeouts.append(timeout)
        clock[0] += 4
        return io.BytesIO(
            b'{"access_token":"fixture-token"}'
            if request.full_url.endswith("/token")
            else b'{"jobId":123}'
        )

    monkeypatch.setattr("scripts.cdc_probe.urllib.request.urlopen", open_request)
    client = AirbyteJobs(Deadline(10, monotonic=lambda: clock[0]))
    assert client.trigger() == {"job_id": 123}
    assert timeouts == [10, 6]


def test_postgres_statement_and_snowflake_query_timeouts_shrink():
    from scripts.cdc_probe import Deadline, postgres_execute, snowflake_fetch

    clock = [0.0]
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params, **kwargs):
            calls.append((sql, params, kwargs))
            clock[0] += 0.5

        def fetchone(self):
            return ("insert", None)

    class Connection:
        def cursor(self):
            return Cursor()

    deadline = Deadline(5, monotonic=lambda: clock[0])
    postgres_execute(Cursor(), "UPDATE fixture SET value=%s", ("one",), deadline)
    postgres_execute(Cursor(), "UPDATE fixture SET value=%s", ("two",), deadline)
    assert [params for sql, params, _ in calls if "set_config" in sql] == [("5000ms",), ("4000ms",)]
    calls.clear()
    snowflake_fetch(Connection(), "synthetic-id", deadline)
    assert [kwargs["timeout"] for _, _, kwargs in calls] == [3, 2]
    clock[0] = 4.5
    with pytest.raises(TimeoutError, match="integer-second"):
        snowflake_fetch(Connection(), "synthetic-id", deadline)
    assert len(calls) == 2, "Subsecond remainder must not become unbounded timeout=0"


def test_probe_checks_deadline_again_before_final_pass(monkeypatch):
    import scripts.cdc_probe as module

    clock = [0.0]
    current = [""]
    complete = [False]
    real_datetime = module.datetime

    class DateTime:
        @staticmethod
        def now(zone):
            if current[0] == "delete" and complete[0]:
                clock[0] = 903.0
            return real_datetime.now(zone)

    def mutate(phase):
        current[0] = phase
        complete[0] = False

    def fetch():
        row = (current[0], "tombstone" if current[0] == "delete" else None)
        return [row, row]

    def done(job):
        complete[0] = True
        return True

    monkeypatch.setattr(module, "datetime", DateTime)
    with pytest.raises(TimeoutError):
        probe(mutate, fetch, dict, job_done=done, monotonic=lambda: clock[0])
