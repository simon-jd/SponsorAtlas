"""Guard skip reasons, ledger arithmetic, STOP writing and the run manifest. No Snowflake here."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from orchestration.sponsoratlas_orchestration import dbt_entry, guard, runtime, trial
from scripts import snowflake_metering

NOW = datetime(2026, 11, 10, 8, tzinfo=UTC)
END = datetime(2026, 11, 24, tzinfo=UTC)


def context(run_id="run-1", dag_id="sponsoratlas_daily"):
    run = SimpleNamespace(
        dag_id=dag_id,
        run_id=run_id,
        run_type="scheduled",
        logical_date=NOW,
        run_after=NOW,
    )
    return {"dag_run": run, "run_id": run_id}


def metered(total=10.0, latest="2026-11-09"):
    return {
        "credits_used_total": total,
        "latest_usage_date": latest,
        "identity": {"account": "ACCT", "user": "COST_OBSERVER_SVC", "warehouse": "SPONSORATLAS_XS"},
        "lag_note": snowflake_metering.LAG_NOTE,
    }


def forbid_connection(*_args, **_kwargs):
    raise AssertionError("No Snowflake connection may be attempted")


@pytest.fixture
def paths(monkeypatch, tmp_path):
    """Every guard path is a temp path: the real STOP and ledger are never read or written."""
    monkeypatch.setattr(guard, "STOP", tmp_path / "STOP")
    monkeypatch.setattr(guard, "LEDGER", tmp_path / "credit-ledger.jsonl")
    monkeypatch.setattr(guard, "STATE", tmp_path / "guard-state.json")
    monkeypatch.setattr(guard, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(runtime, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(runtime, "ALERT_LOG", tmp_path / "alerts/alerts.jsonl")
    monkeypatch.setattr(guard, "read_metered", lambda: metered())
    for key, value in {
        "SPONSORATLAS_ORCHESTRATION_MODE": "production",
        "DBT_TARGET": "snowflake",
        "SPONSORATLAS_ALERT_TRANSPORT": "local_log",
        "SNOWFLAKE_TRIAL_STARTED_AT": "2026-10-25T07:29:00Z",
        "SNOWFLAKE_TRIAL_EXPIRES_AT": "2026-11-24T00:00:00Z",
    }.items():
        monkeypatch.setenv(key, value)
    return tmp_path


def alerts(paths):
    log = paths / "alerts/alerts.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def seed_ledger(credits):
    guard.open_run("sponsoratlas_daily", "seed", NOW - timedelta(days=1))
    rows = guard.read_ledger()
    rows[0].update(status="closed", estimated_credits=credits)
    guard._write_ledger(rows)


def test_admitted_run_opens_one_ledger_row_even_on_retry(paths):
    assert guard.preflight(now=NOW, **context()) is True
    assert guard.preflight(now=NOW + timedelta(seconds=10), **context()) is True
    rows = guard.read_ledger()
    assert len(rows) == 1
    assert rows[0]["started_at"] == NOW.isoformat()
    assert not guard.STOP.exists()
    assert guard.read_state()["last_metered"]["credits_used_total"] == 10.0


@pytest.mark.parametrize("marker", ["", "operator stopped this"])
def test_stop_marker_skips_and_is_left_exactly_as_found(paths, monkeypatch, marker):
    guard.STOP.write_text(marker)
    monkeypatch.setattr(guard, "read_metered", forbid_connection)
    assert guard.preflight(now=NOW, **context()) is False
    assert guard.STOP.read_text() == marker
    assert guard.read_ledger() == []
    assert alerts(paths) == []


@pytest.mark.parametrize(
    ("left", "admitted"),
    [(timedelta(seconds=3599), False), (timedelta(seconds=3600), True)],
)
def test_under_one_hour_of_trial_skips_without_stop(paths, left, admitted):
    assert guard.preflight(now=END - left, **context()) is admitted
    assert not guard.STOP.exists()


def test_production_preflight_with_past_stamps_never_connects(paths, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_TRIAL_STARTED_AT", "2026-09-01T00:00:00Z")
    monkeypatch.setenv("SNOWFLAKE_TRIAL_EXPIRES_AT", "2026-09-30T00:00:00Z")
    monkeypatch.setattr(guard, "read_metered", forbid_connection)
    monkeypatch.setattr(guard.subprocess, "run", forbid_connection)
    assert guard.preflight(**context()) is False
    assert guard.read_ledger() == []
    assert not guard.STOP.exists()


def test_missing_trial_stamps_fail_rather_than_skip(paths, monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_TRIAL_EXPIRES_AT")
    monkeypatch.setattr(guard, "read_metered", forbid_connection)
    with pytest.raises(RuntimeError, match="SNOWFLAKE_TRIAL_EXPIRES_AT"):
        guard.preflight(now=NOW, **context())


def test_missing_alert_routing_fails_before_external_work(paths, monkeypatch):
    monkeypatch.delenv("SPONSORATLAS_ALERT_TRANSPORT")
    monkeypatch.setattr(guard, "read_metered", forbid_connection)
    with pytest.raises(RuntimeError, match="SPONSORATLAS_ALERT_TRANSPORT"):
        guard.preflight(now=NOW, **context())


def test_ledger_limit_writes_stop_and_alert_before_any_connection(paths, monkeypatch):
    seed_ledger(2.5)
    monkeypatch.setattr(guard, "read_metered", forbid_connection)
    assert guard.preflight(now=NOW, **context("over")) is False
    message = json.loads(guard.STOP.read_text())
    assert message["reason"] == "ledger_limit"
    assert "ESTIMATED ledger total 2.5000" in message["detail"]
    assert message["run_id"] == "over"
    assert [a["event"] for a in alerts(paths)] == ["guard_stop"]
    assert alerts(paths)[0]["delivery"] == "LOCAL_LOG_NOT_DELIVERED"
    assert "ledger_limit" in alerts(paths)[0]["detail"]
    assert len(guard.read_ledger()) == 1


def test_just_under_ledger_limit_runs(paths):
    seed_ledger(2.49)
    assert guard.preflight(now=NOW, **context()) is True


@pytest.mark.parametrize(
    ("dag_id", "total", "admitted"),
    [
        ("sponsoratlas_daily", 122.48, True),
        ("sponsoratlas_daily", 122.49, False),
        ("sponsoratlas", 122.23, True),
        ("sponsoratlas", 122.24, False),
        ("sponsoratlas_daily", 123.0, False),
    ],
)
def test_account_metered_limit_leaves_room_for_the_whole_run(
    paths, monkeypatch, dag_id, total, admitted
):
    monkeypatch.setattr(guard, "read_metered", lambda: metered(total))
    assert guard.preflight(now=NOW, **context(dag_id=dag_id)) is admitted
    if admitted:
        assert not guard.STOP.exists()
        return
    message = json.loads(guard.STOP.read_text())
    assert message["reason"] == "account_limit"
    assert "123.0" in message["detail"] and "lags" in message["detail"]
    assert [a["event"] for a in alerts(paths)] == ["guard_stop"]
    assert guard.read_ledger() == []


def test_two_consecutive_connection_failures_write_stop(paths, monkeypatch):
    def failing():
        raise guard.MeteringFailure("metering exit 2: OperationalError")

    monkeypatch.setattr(guard, "read_metered", failing)
    for _attempt in range(3):  # Retries of one run are one failure.
        with pytest.raises(RuntimeError, match="failure 1"):
            guard.preflight(now=NOW, **context("first"))
    assert not guard.STOP.exists()
    assert guard.preflight(now=NOW + timedelta(days=1), **context("second")) is False
    message = json.loads(guard.STOP.read_text())
    assert message["reason"] == "connection_failures"
    assert "2 consecutive" in message["detail"] and "OperationalError" in message["detail"]
    assert [a["event"] for a in alerts(paths)] == ["guard_stop"]


def test_two_dags_failing_on_one_utc_date_are_one_outage(paths, monkeypatch):
    def failing():
        raise guard.MeteringFailure("metering exit 2: OperationalError")

    monkeypatch.setattr(guard, "read_metered", failing)
    sunday = datetime(2026, 11, 8, 8, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="failure 1"):
        guard.preflight(now=sunday, **context("daily"))
    with pytest.raises(RuntimeError, match="failure 1"):
        guard.preflight(now=sunday + timedelta(hours=1), **context("weekly", dag_id="sponsoratlas"))
    assert not guard.STOP.exists()
    monkeypatch.setattr(guard, "read_metered", lambda: metered(latest="2026-11-07"))
    assert guard.preflight(now=sunday + timedelta(days=1), **context("monday")) is True
    assert guard.read_state()["connection_failures"] == 0


@pytest.mark.parametrize(
    "reading",
    [metered(total=0.0), metered(latest=None), metered(total=0.0, latest=None)],
    ids=["zero", "no-date", "empty-view"],
)
def test_an_empty_metering_result_fails_closed(paths, monkeypatch, reading):
    monkeypatch.setattr(guard, "read_metered", lambda: reading)
    with pytest.raises(RuntimeError, match="empty total"):
        guard.preflight(now=NOW, **context())
    assert guard.read_ledger() == []
    assert "last_metered" not in guard.read_state()


def test_a_stale_view_fails_closed_once_our_own_runs_should_show(paths, monkeypatch):
    stale = metered(latest="2026-10-26")
    monkeypatch.setattr(guard, "read_metered", lambda: stale)
    # An idle account before this deployment says nothing about the view: the first runs go ahead.
    assert guard.preflight(now=NOW, **context("first")) is True
    assert guard.preflight(now=NOW + timedelta(days=1), **context("second")) is True
    with pytest.raises(RuntimeError, match="stale"):
        guard.preflight(now=NOW + timedelta(days=2), **context("third"))
    assert len(guard.read_ledger()) == 2
    # Our run from two days ago now shows, so the same date is fresh.
    monkeypatch.setattr(guard, "read_metered", lambda: metered(latest=NOW.date().isoformat()))
    assert guard.preflight(now=NOW + timedelta(days=2, hours=1), **context("third")) is True


def test_the_first_metered_read_stays_as_the_baseline(paths, monkeypatch):
    assert guard.preflight(now=NOW, **context("first")) is True
    monkeypatch.setattr(guard, "read_metered", lambda: metered(total=11.0))
    assert guard.preflight(now=NOW + timedelta(days=1), **context("second")) is True
    state = guard.read_state()
    assert state["metered_baseline"]["credits_used_total"] == 10.0
    assert state["metered_baseline"]["recorded_at"] == NOW.isoformat()
    assert state["last_metered"]["credits_used_total"] == 11.0


def test_a_good_read_resets_the_connection_failure_count(paths, monkeypatch):
    def failing():
        raise guard.MeteringFailure("down")

    monkeypatch.setattr(guard, "read_metered", failing)
    with pytest.raises(RuntimeError):
        guard.preflight(now=NOW, **context("first"))
    monkeypatch.setattr(guard, "read_metered", lambda: metered())
    assert guard.preflight(now=NOW, **context("second")) is True
    monkeypatch.setattr(guard, "read_metered", failing)
    with pytest.raises(RuntimeError, match="failure 1"):
        guard.preflight(now=NOW, **context("third"))
    assert not guard.STOP.exists()


def test_trip_keeps_an_existing_reason_and_stop_survives_a_lost_alert(paths, monkeypatch, capsys):
    guard.STOP.write_text("first reason")
    guard._trip(context(), "ledger_limit", "second reason", NOW)
    assert guard.STOP.read_text() == "first reason"
    guard.STOP.unlink()
    monkeypatch.delenv("SPONSORATLAS_ALERT_TRANSPORT")
    assert guard._trip(context(), "ledger_limit", "why", NOW) is False
    assert json.loads(guard.STOP.read_text())["reason"] == "ledger_limit"
    assert "ALERT_FAILED" in capsys.readouterr().out


def test_ledger_arithmetic_and_unclosed_run_charge(paths):
    guard.open_run("sponsoratlas_daily", "closed", NOW)
    guard.open_run("sponsoratlas", "unclosed", NOW)
    assert guard.ledger_total() == pytest.approx(0.5)
    row = guard.close_run("sponsoratlas_daily", "closed", NOW + timedelta(seconds=540))
    assert row["wall_seconds"] == 540
    assert row["estimated_credits"] == pytest.approx((540 + 60) / 3600)
    assert guard.ledger_total() == pytest.approx(600 / 3600 + 900 / 3600)
    assert [line["run_id"] for line in guard.read_ledger()] == ["closed", "unclosed"]
    assert all("ESTIMATE" in line["basis"] for line in guard.read_ledger())
    assert guard.LEDGER.stat().st_mode & 0o777 == 0o600
    assert len(guard.LEDGER.read_text().splitlines()) == 2


def test_a_run_repaired_the_next_day_is_charged_at_most_its_timeout(paths):
    assert guard.preflight(now=NOW, **context()) is True
    row = guard.close_run("sponsoratlas_daily", "run-1", NOW + timedelta(hours=26))
    assert row["wall_seconds"] == 26 * 3600
    assert row["estimated_credits"] == pytest.approx((30 * 60 + 60) / 3600)
    assert guard.preflight(now=NOW + timedelta(hours=27), **context("next")) is True
    assert not guard.STOP.exists()


@pytest.mark.parametrize(
    ("dag_id", "minutes", "credits"),
    [
        ("sponsoratlas_daily", 3, 0.25),  # Never less than a run nobody closed.
        ("sponsoratlas_daily", 25, 26 / 60),
        ("sponsoratlas_daily", 600, 31 / 60),
        ("sponsoratlas", 600, 46 / 60),
    ],
)
def test_a_failed_run_is_charged_its_wall_time_within_its_cap(paths, dag_id, minutes, credits):
    assert guard.preflight(now=NOW, **context(dag_id=dag_id)) is True
    row = guard.fail_run(dag_id, "run-1", NOW + timedelta(minutes=minutes))
    assert row["status"] == "failed"
    assert row["estimated_credits"] == pytest.approx(credits)
    assert guard.ledger_total() == pytest.approx(credits)


def test_slow_failures_trip_the_ledger_within_days_not_weeks(paths, monkeypatch):
    admitted = 0
    for day in range(10):
        now = NOW + timedelta(days=day)
        monkeypatch.setattr(guard, "read_metered", lambda: metered(latest=now.date().isoformat()))  # noqa: B023
        if not guard.preflight(now=now, **context(f"run-{day}")):
            break
        admitted += 1
        guard.fail_run("sponsoratlas_daily", f"run-{day}", now + timedelta(minutes=30))
    assert admitted == 5
    assert json.loads(guard.STOP.read_text())["reason"] == "ledger_limit"


@pytest.mark.parametrize("full_context", [False, True], ids=["minimal", "template"])
def test_failure_callback_settles_the_row_with_airflows_context(paths, full_context):
    assert guard.preflight(now=NOW, **context()) is True
    dag = SimpleNamespace(dag_id="sponsoratlas_daily")
    callback = {"dag": dag, "run_id": "run-1", "reason": "timed_out"}
    if full_context:
        callback |= {"dag_run": context()["dag_run"], "task_instance": SimpleNamespace(task_id="x")}
    guard.on_run_failure(callback)
    assert guard.read_ledger()[0]["status"] == "failed"
    alert = alerts(paths)[0]
    assert (alert["event"], alert["detail"]) == ("run_failure", "timed_out")
    assert (alert["dag_id"], alert["run_id"]) == ("sponsoratlas_daily", "run-1")


def test_failure_callback_with_no_ledger_row_still_alerts(paths):
    dag = SimpleNamespace(dag_id="sponsoratlas_daily")
    guard.on_run_failure({"dag": dag, "run_id": "never-admitted", "reason": "task_failure"})
    assert guard.read_ledger() == []
    assert len(alerts(paths)) == 1


def test_stop_written_mid_run_refuses_later_paid_work(paths, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(dbt_entry.subprocess, "Popen", lambda *a, **k: calls.append(a))
    trial.STOP.write_text("")
    ctx = {"run_id": "run-1", "logical_date": NOW, "dag_run": context()["dag_run"]}
    with pytest.raises(RuntimeError, match="STOP"):
        runtime.load_boards(**ctx)
    with pytest.raises(RuntimeError, match="STOP"):
        dbt_entry.main(["build"])
    monkeypatch.setenv("SEMANTIC_SOURCE", "snowflake")
    monkeypatch.setenv("SEMANTIC_WORK", str(paths / "work"))
    with pytest.raises(RuntimeError, match="STOP"):
        runtime.cube_refresh(**ctx)
    assert calls == []


def test_the_real_stop_path_is_the_guards(monkeypatch):
    monkeypatch.undo()
    assert trial.STOP == guard.STOP


def test_closing_an_unknown_run_fails(paths):
    with pytest.raises(RuntimeError, match="No ledger row"):
        guard.close_run("sponsoratlas_daily", "never-opened", NOW)


def test_record_usage_closes_the_row_and_writes_a_manifest(paths, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_COST_OBSERVER_PRIVATE_KEY_FILE", "/secret/path/key.p8")
    run_id = "scheduled__2026-11-10T08:00:00+00:00"
    assert guard.preflight(now=NOW, **context(run_id)) is True
    directory = runtime.run_dir(run_id)
    directory.mkdir(parents=True)
    (directory / "board-manifest.json").write_text(
        json.dumps({"status": "LOADED", "totals": {"rows": 86}, "table_count": 86, "boards": []})
    )
    path = guard.record_usage(now=NOW + timedelta(seconds=240), **context(run_id))
    assert path.endswith("runs/scheduled__2026-11-10T08_00_00_00_00.json")
    text = (paths / "evidence/runs/scheduled__2026-11-10T08_00_00_00_00.json").read_text()
    manifest = json.loads(text)
    assert manifest["dag_id"] == "sponsoratlas_daily"
    assert manifest["run_id"] == run_id
    assert manifest["run_type"] == "scheduled"
    assert manifest["logical_date"] == NOW.isoformat()
    assert manifest["start"] == NOW.isoformat()
    assert manifest["end"] == (NOW + timedelta(seconds=240)).isoformat()
    assert manifest["ledger"]["estimated_credits"] == pytest.approx(300 / 3600)
    assert "ESTIMATE" in manifest["ledger"]["basis"]
    assert manifest["metered_total"]["credits_used_total"] == 10.0
    assert "lags" in manifest["metered_total"]["lag_note"]
    assert manifest["board_manifest_totals"] == {
        "status": "LOADED",
        "mode": None,
        "target": None,
        "totals": {"rows": 86},
        "table_count": 86,
    }
    assert manifest["orchestration_mode"] == "production"
    assert manifest["dbt_target"] == "snowflake"
    assert manifest["metered_total"]["identity"]["warehouse"] == "SPONSORATLAS_XS"
    assert manifest["metered_total"]["identity"]["account"] == "redacted"
    assert manifest["metered_baseline"]["identity"]["account"] == "redacted"
    assert '"ACCT"' not in text
    assert guard.read_state()["last_metered"]["identity"]["account"] == "ACCT"
    assert manifest["metered_baseline"]["credits_used_total"] == 10.0
    assert len(manifest["git_head"]) == 40
    assert len(manifest["git_diff_sha256"]) == 64
    assert manifest["dbt_core"] and manifest["project_python"] and manifest["airflow_python"]
    assert "/secret/path" not in text and "PRIVATE" not in text
    assert guard.read_ledger()[0]["status"] == "closed"


def test_record_usage_without_a_board_manifest(paths, monkeypatch):
    guard.preflight(now=NOW, **context(dag_id="sponsoratlas"))
    guard.record_usage(now=NOW, **context(dag_id="sponsoratlas"))
    manifest = json.loads((paths / "evidence/runs/run-1.json").read_text())
    assert manifest["board_manifest_totals"] is None


def test_read_metered_reports_failure_types_only(monkeypatch):
    def fake(stdout, code):
        return lambda *_a, **_k: SimpleNamespace(stdout=stdout, returncode=code)

    monkeypatch.setattr(guard.subprocess, "run", fake('{"credits_used_total": "12.5"}', 0))
    assert guard.read_metered()["credits_used_total"] == 12.5
    monkeypatch.setattr(
        guard.subprocess,
        "run",
        fake('{"status": "failed", "error_type": "OperationalError"}', 2),
    )
    with pytest.raises(guard.MeteringFailure, match="OperationalError"):
        guard.read_metered()
    monkeypatch.setattr(guard.subprocess, "run", fake("not json", 1))
    with pytest.raises(guard.MeteringFailure, match="unreadable"):
        guard.read_metered()


def test_metering_script_with_a_mocked_connector(monkeypatch):
    executed, options = [], {}

    class Cursor:
        def execute(self, sql):
            executed.append(sql)

        def fetchone(self):
            if "CURRENT_ACCOUNT" in executed[-1]:
                return ("ACCT", "COST_OBSERVER_SVC", "SPONSORATLAS_XS")
            return (12.5, "2026-11-05")

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

        def close(self):
            Connection.closed = True

    def connect(**kwargs):
        options.update(kwargs)
        return Connection()

    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "example-account")
    monkeypatch.setenv("SNOWFLAKE_COST_OBSERVER_PRIVATE_KEY_FILE", "/keys/COST_OBSERVER_SVC.p8")
    result = snowflake_metering.read_total(connect)
    assert result["credits_used_total"] == 12.5
    assert result["latest_usage_date"] == "2026-11-05"
    assert result["identity"] == {
        "account": "ACCT",
        "user": "COST_OBSERVER_SVC",
        "warehouse": "SPONSORATLAS_XS",
    }
    assert "lags" in result["lag_note"]
    assert "SPCS" in result["scope"]
    json.dumps(result)
    assert "SNOWFLAKE.ACCOUNT_USAGE.METERING_DAILY_HISTORY" in executed[0]
    assert "SUM(CREDITS_USED)" in executed[0]
    assert (options["user"], options["role"], options["warehouse"]) == (
        "COST_OBSERVER_SVC",
        "COST_OBSERVER",
        "SPONSORATLAS_XS",
    )
    assert Connection.closed


def export_runs_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "export_runs", guard.ROOT / "orchestration/bin/export_runs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snowflake_manifest(dag_id="sponsoratlas"):
    return {
        "dag_id": dag_id,
        "orchestration_mode": "production",
        "dbt_target": "snowflake",
        "metered_total": metered(),
        "board_manifest_totals": {"mode": "snowflake"} if dag_id == "sponsoratlas_daily" else None,
    }


def test_export_counts_a_skipped_run_as_not_all_success():
    module = export_runs_module()
    run = {"run_id": "scheduled__x", "state": "success", "run_after": "2026-11-08T09:00:00+00:00"}
    skipped = module.summarize_run(
        run,
        [
            {"task_id": "preflight", "state": "success"},
            {"task_id": "load_boards", "state": "skipped"},
        ],
    )
    assert skipped["all_success"] is False
    assert skipped["counts_as_evidence"] is False
    assert skipped["run_type"] == "scheduled"
    assert module.summarize_run(run, [])["all_success"] is False
    success = [{"task_id": "preflight", "state": "success"}]
    good = module.summarize_run(run, success, snowflake_manifest())
    assert good["all_success"] is True
    assert good["counts_as_evidence"] is True
    rehearsal = module.summarize_run(
        {**run, "run_after": "2026-10-24T20:16:52+00:00"}, success, snowflake_manifest()
    )
    assert rehearsal["all_success"] is True
    assert rehearsal["counts_as_evidence"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"dbt_target": "local"},
        {"orchestration_mode": "synthetic_http"},
        {"metered_total": None},
        {"metered_total": {"identity": {"account": "ACCT", "warehouse": "OTHER"}}},
        {"board_manifest_totals": {"mode": "duckdb-dry-run"}},
        {"board_manifest_totals": None},
    ],
)
def test_a_run_that_never_touched_snowflake_is_not_evidence(changes):
    module = export_runs_module()
    run = {"run_id": "manual__2026-11-07", "state": "success", "run_after": "2026-11-07T00:00:00+00:00"}
    tasks = [{"task_id": "load_boards", "state": "success"}]
    manifest = {**snowflake_manifest("sponsoratlas_daily"), **changes}
    summary = module.summarize_run(run, tasks, manifest)
    assert summary["all_success"] is True and summary["counts_as_evidence"] is False
    assert module.summarize_run(run, tasks, None)["counts_as_evidence"] is False


def test_run_manifest_is_matched_by_run_and_dag(paths):
    module = export_runs_module()
    (paths / "evidence/runs").mkdir(parents=True)
    (paths / "evidence/runs/run-1.json").write_text(json.dumps(snowflake_manifest()))
    assert module.run_manifest("sponsoratlas", "run-1")["dag_id"] == "sponsoratlas"
    assert module.run_manifest("sponsoratlas_daily", "run-1") is None
    assert module.run_manifest("sponsoratlas", "absent") is None
