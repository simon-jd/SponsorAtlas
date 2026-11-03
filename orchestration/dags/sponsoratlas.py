"""Daily public pipeline. Parsing renders dbt metadata only; all I/O is runtime opt-in."""

import os
from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import InvocationMode, LoadMode, TestBehavior, TestIndirectSelection
from cosmos.operators.local import DbtTestLocalOperator

from orchestration.sponsoratlas_orchestration.runtime import (
    QUALITY_GATES,
    ROOT,
    airbyte_sync,
    cube_refresh,
    deadline_alert,
    freshness,
    notify,
)

DBT = str(ROOT / "orchestration/bin/dbt")
RENDER_DBT = str(ROOT / ".venv/bin/dbt")
PROFILE = ProfileConfig(
    profile_name="sponsoratlas",
    target_name=os.environ.get("DBT_TARGET", "snowflake"),
    profiles_yml_filepath=ROOT / "dbt/profiles.yml",
)
SELECTION = ["tag:staging", "tag:dimensions", "tag:facts"]
ARTIFACTS = (
    str(ROOT / ".tools/orchestration/artifacts")
    + "/{{ run_id | replace('/', '_') | replace('..', '_') }}/{{ task.task_id }}"
)
DBT_ENV = {"DBT_TARGET_PATH": ARTIFACTS + "/target", "DBT_LOG_PATH": ARTIFACTS + "/logs"}
with DAG(
    dag_id="sponsoratlas",
    schedule="0 8 * * *",
    start_date=datetime(2024, 10, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(seconds=10),
        "execution_timeout": timedelta(minutes=15),
        "on_failure_callback": notify,
    },
    on_failure_callback=notify,
    deadline=DeadlineAlert(
        reference=DeadlineReference.DAGRUN_QUEUED_AT,
        interval=timedelta(minutes=30),
        callback=SyncCallback(deadline_alert),
    ),
    tags=["sponsoratlas", "public"],
) as dag:
    sync = PythonOperator(task_id="airbyte_sync", python_callable=airbyte_sync)
    fresh = PythonOperator(task_id="jobs_source_freshness", python_callable=freshness)
    public = DbtTaskGroup(
        group_id="dbt_public",
        project_config=ProjectConfig(
            ROOT / "dbt",
            install_dbt_deps=False,
            partial_parse=False,
            dbt_vars={"enable_private_applications": False},
        ),
        profile_config=PROFILE,
        execution_config=ExecutionConfig(
            dbt_executable_path=DBT,
            invocation_mode=InvocationMode.SUBPROCESS,
            test_indirect_selection=TestIndirectSelection.CAUTIOUS,
        ),
        render_config=RenderConfig(
            load_method=LoadMode.DBT_LS,
            dbt_executable_path=RENDER_DBT,
            invocation_mode=InvocationMode.SUBPROCESS,
            select=SELECTION,
            test_behavior=TestBehavior.BUILD,
            emit_datasets=False,
        ),
        operator_args={
            "install_deps": False,
            "append_env": True,
            "partial_parse": False,
            "env": DBT_ENV,
        },
    )
    # Cautious per-node builds cannot include multi-parent invariants. Select every
    # reviewed singular gate explicitly once all public parents have materialized.
    quality = DbtTestLocalOperator(
        task_id="public_quality_gates",
        project_dir=str(ROOT / "dbt"),
        profile_config=PROFILE,
        dbt_executable_path=DBT,
        invocation_mode=InvocationMode.SUBPROCESS,
        select=SELECTION + QUALITY_GATES,
        indirect_selection="cautious",
        dbt_vars={"enable_private_applications": False},
        install_deps=False,
        append_env=True,
        partial_parse=False,
        env=DBT_ENV,
    )
    refresh = PythonOperator(task_id="cube_refresh", python_callable=cube_refresh)
    sync >> fresh >> public >> quality >> refresh
