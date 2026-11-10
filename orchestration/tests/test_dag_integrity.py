"""Import the real Airflow SDK DAGs and Cosmos graph, without service credentials."""

import importlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from airflow.callbacks.callback_requests import DagCallbackRequest
from airflow.dag_processing.processor import _execute_dag_callbacks
from airflow.providers.standard.operators.python import ShortCircuitOperator
from airflow.timetables.base import TimeRestriction
from airflow.timetables.trigger import CronTriggerTimetable

from orchestration.sponsoratlas_orchestration import guard, runtime

VARS = {"enable_private_applications": False, "snapshot_schema": "INTERMEDIATE"}


def load(name):
    return importlib.import_module("orchestration.dags." + name).dag


def scheduled_runs(dag):
    """Every point the schedule allows between start_date and end_date, at any unpause time."""
    timetable = CronTriggerTimetable(dag.schedule, timezone="UTC")
    restriction = TimeRestriction(earliest=dag.start_date, latest=dag.end_date, catchup=True)
    runs, last = [], None
    while info := timetable.next_dagrun_info(
        last_automated_data_interval=last, restriction=restriction
    ):
        runs.append(info.logical_date)
        last = info.data_interval
    return runs


def assert_common_rules(dag):
    assert dag.topological_sort()
    assert all(task.retries >= 2 for task in dag.tasks)
    assert dag.deadline is not None
    assert dag.on_failure_callback in (guard.on_run_failure, [guard.on_run_failure])
    assert dag.dagrun_timeout == timedelta(minutes=guard.RUN_MINUTES[dag.dag_id])
    assert dag.max_active_runs == 1
    assert dag.catchup is False
    first = dag.get_task("preflight")
    assert isinstance(first, ShortCircuitOperator)
    assert first.retry_delay == timedelta(minutes=5)
    assert first.ignore_downstream_trigger_rules is True
    assert not first.upstream_task_ids
    assert first.get_flat_relative_ids(upstream=False) == set(dag.task_ids) - {"preflight"}
    usage = dag.get_task("record_usage")
    assert usage.trigger_rule == "all_success"
    assert not usage.downstream_task_ids
    ids = set(dag.task_ids)
    assert not any("private" in t or "my_applications" in t for t in ids)
    assert not {"airbyte_sync", "jobs_source_freshness"} & ids


def test_full_build_dag_integrity():
    dag = load("sponsoratlas")
    assert dag.dag_id == "sponsoratlas"
    assert_common_rules(dag)
    assert dag.schedule == "0 9 * * 0"
    assert dag.end_date == datetime(2026, 11, 16, tzinfo=UTC)
    assert scheduled_runs(dag) == [
        datetime(2026, 11, 8, 9, tzinfo=UTC),
        datetime(2026, 11, 15, 9, tzinfo=UTC),
    ]
    assert dag.get_task("observations_freshness").upstream_task_ids == {"preflight"}
    refresh = dag.get_task("cube_refresh")
    assert refresh.trigger_rule == "all_success"
    assert refresh.upstream_task_ids == {"public_quality_gates"}
    assert dag.get_task("record_usage").upstream_task_ids == {"cube_refresh"}
    assert "dbt_public" in dag.task_group.children
    ids = set(dag.task_ids)
    assert any("fact_lca_case" in t for t in ids)
    assert any("fact_job_posting_daily" in t for t in ids)
    assert any("fact_wage_benchmark" in t for t in ids)
    for gate in [
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
    ]:
        assert gate in dag.get_task("public_quality_gates").select, gate


def test_daily_dag_integrity():
    dag = load("sponsoratlas_daily")
    assert dag.dag_id == "sponsoratlas_daily"
    assert_common_rules(dag)
    assert dag.schedule == "0 8 * * *"
    assert dag.end_date == datetime(2026, 11, 23, tzinfo=UTC)
    runs = scheduled_runs(dag)
    assert runs[0] == datetime(2026, 11, 6, 8, tzinfo=UTC)
    assert runs[-1] == datetime(2026, 11, 22, 8, tzinfo=UTC)
    assert len(runs) == 17
    order = [
        "preflight",
        "load_boards",
        "observations_freshness",
        "dbt_job_fact",
        "job_gates",
        "record_usage",
    ]
    assert set(dag.task_ids) == set(order)
    for before, after in zip(order, order[1:], strict=False):
        assert dag.get_task(after).upstream_task_ids == {before}
    fact = dag.get_task("dbt_job_fact")
    assert fact.base_cmd == ["build"]
    assert fact.select == ["fact_job_posting_daily"]
    gates = dag.get_task("job_gates")
    assert gates.base_cmd == ["test"]
    assert gates.select == [
        "facts_grain",
        "facts_job_observation_accounting",
        "facts_job_fields_present",
    ]


@pytest.mark.parametrize("dag_id", ["sponsoratlas_daily", "sponsoratlas"])
def test_airflows_own_dag_callback_path_settles_the_ledger(monkeypatch, tmp_path, dag_id):
    """Run the callback the way the DAG processor does, as on a dagrun_timeout."""
    monkeypatch.setattr(guard, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(runtime, "ALERT_LOG", tmp_path / "alerts.jsonl")
    monkeypatch.setenv("SPONSORATLAS_ALERT_TRANSPORT", "local_log")
    guard.open_run(dag_id, "run-1", datetime.now(UTC) - timedelta(minutes=20))
    request = DagCallbackRequest(
        filepath="unused.py",
        bundle_name="unused",
        bundle_version=None,
        dag_id=dag_id,
        run_id="run-1",
        msg="timed_out",
    )
    log = MagicMock()
    _execute_dag_callbacks(SimpleNamespace(dags={dag_id: load(dag_id)}), request, log)
    log.exception.assert_not_called()
    row = guard.read_ledger()[0]
    assert row["status"] == "failed"
    assert row["estimated_credits"] == pytest.approx(21 / 60, abs=0.01)
    alert = json.loads((tmp_path / "alerts.jsonl").read_text())
    assert (alert["event"], alert["detail"], alert["dag_id"]) == ("run_failure", "timed_out", dag_id)


def test_every_dbt_operator_really_passes_the_vars():
    """A Cosmos operator takes vars=. dbt_vars= is swallowed, and the snapshot then resolves to
    the SNAPSHOTS schema that does not exist on Snowflake."""
    for name, task_ids in {
        "sponsoratlas": ["public_quality_gates", "dbt_public.snap_employer_snapshot_build"],
        "sponsoratlas_daily": ["dbt_job_fact", "job_gates"],
    }.items():
        dag = load(name)
        for task_id in task_ids:
            assert dag.get_task(task_id).vars == VARS, task_id


def test_opt_in_deadline_probe_has_executable_callable(monkeypatch):
    import inspect

    monkeypatch.setenv("SPONSORATLAS_ENABLE_DEADLINE_PROBE", "1")
    module = importlib.import_module("orchestration.dags.deadline_probe")
    dag = importlib.reload(module).dag
    assert all(t.retries >= 2 for t in dag.tasks)
    inspect.signature(dag.tasks[0].python_callable)


def test_dbt_artifacts_are_isolated_per_task_and_partial_parse_disabled():
    for name in ["sponsoratlas", "sponsoratlas_daily"]:
        tasks = [t for t in load(name).tasks if hasattr(t, "partial_parse")]
        assert tasks
        for task in tasks:
            assert task.partial_parse is False
            assert "{{ task.task_id }}" in task.env["DBT_TARGET_PATH"]
            assert "{{ run_id" in task.env["DBT_TARGET_PATH"]
