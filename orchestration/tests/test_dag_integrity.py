"""Import the real Airflow SDK DAG and Cosmos graph, without service credentials."""

import importlib


def test_dag_integrity():
    dag = importlib.import_module("orchestration.dags.sponsoratlas").dag
    assert dag.dag_id == "sponsoratlas"
    assert dag.topological_sort()
    assert all(task.retries >= 2 for task in dag.tasks)
    assert dag.deadline is not None
    assert dag.on_failure_callback is not None
    assert dag.max_active_runs == 1
    freshness = dag.get_task("jobs_source_freshness")
    assert freshness.upstream_task_ids == {"airbyte_sync"}
    refresh = dag.get_task("cube_refresh")
    assert refresh.trigger_rule == "all_success"
    assert refresh.upstream_task_ids
    assert refresh.upstream_task_ids == {"public_quality_gates"}
    assert "dbt_public" in dag.task_group.children
    ids = set(dag.task_ids)
    assert any("fact_lca_case" in t for t in ids)
    assert any("fact_job_posting_daily" in t for t in ids)
    assert any("fact_wage_benchmark" in t for t in ids)
    for gate in [
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
    ]:
        assert gate in dag.get_task("public_quality_gates").select, gate
    assert not any("private" in t or "my_applications" in t for t in ids)


def test_opt_in_deadline_probe_has_executable_callable(monkeypatch):
    import inspect

    monkeypatch.setenv("SPONSORATLAS_ENABLE_DEADLINE_PROBE", "1")
    module = importlib.import_module("orchestration.dags.deadline_probe")
    dag = importlib.reload(module).dag
    assert all(t.retries >= 2 for t in dag.tasks)
    inspect.signature(dag.tasks[0].python_callable)


def test_dbt_artifacts_are_isolated_per_task_and_partial_parse_disabled():
    dag = importlib.import_module("orchestration.dags.sponsoratlas").dag
    tasks = [t for t in dag.tasks if hasattr(t, "partial_parse")]
    assert tasks
    for task in tasks:
        assert task.partial_parse is False
        assert "{{ task.task_id }}" in task.env["DBT_TARGET_PATH"]
        assert "{{ run_id" in task.env["DBT_TARGET_PATH"]
