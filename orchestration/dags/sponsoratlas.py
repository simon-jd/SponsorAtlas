"""Weekly full public build. Parsing renders dbt metadata only; all I/O is runtime opt-in.

preflight skips the whole run on any guard reason. The Airbyte task is retired from this DAG:
no Airbyte credentials exist, and job observations arrive through sponsoratlas_daily.
"""

import os
from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator, ShortCircuitOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import InvocationMode, LoadMode, TestBehavior, TestIndirectSelection
from cosmos.operators.local import DbtTestLocalOperator

from orchestration.sponsoratlas_orchestration.guard import (
    RUN_MINUTES,
    on_run_failure,
    preflight,
    record_usage,
)
from orchestration.sponsoratlas_orchestration.runtime import (
    DBT_ENV,
    DBT_VARS,
    QUALITY_GATES,
    ROOT,
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
with DAG(
    dag_id="sponsoratlas",
    # Sunday 09:00 UTC. start_date and end_date allow only Nov 8 and Nov 15; an earlier start
    # would make Airflow run the latest past Sunday as soon as the DAG is unpaused.
    schedule="0 9 * * 0",
    start_date=datetime(2026, 11, 6, tzinfo=UTC),
    end_date=datetime(2026, 11, 16, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(seconds=10),
        "execution_timeout": timedelta(minutes=15),
        "on_failure_callback": notify,
    },
    on_failure_callback=on_run_failure,
    # Unlike the deadline below, this ends the run: it is the hard bound on spend per run.
    dagrun_timeout=timedelta(minutes=RUN_MINUTES["sponsoratlas"]),
    deadline=DeadlineAlert(
        reference=DeadlineReference.DAGRUN_QUEUED_AT,
        interval=timedelta(minutes=30),
        callback=SyncCallback(deadline_alert),
    ),
    tags=["sponsoratlas", "public"],
) as dag:
    # Five minutes between attempts, so a short Snowflake outage does not use up the retries.
    guard = ShortCircuitOperator(
        task_id="preflight", python_callable=preflight, retry_delay=timedelta(minutes=5)
    )
    fresh = PythonOperator(task_id="observations_freshness", python_callable=freshness)
    public = DbtTaskGroup(
        group_id="dbt_public",
        project_config=ProjectConfig(
            ROOT / "dbt",
            install_dbt_deps=False,
            partial_parse=False,
            dbt_vars=DBT_VARS,
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
        vars=DBT_VARS,  # An operator takes vars=; it silently drops dbt_vars=.
        install_deps=False,
        append_env=True,
        partial_parse=False,
        env=DBT_ENV,
    )
    refresh = PythonOperator(task_id="cube_refresh", python_callable=cube_refresh)
    usage = PythonOperator(task_id="record_usage", python_callable=record_usage)
    guard >> fresh >> public >> quality >> refresh >> usage
