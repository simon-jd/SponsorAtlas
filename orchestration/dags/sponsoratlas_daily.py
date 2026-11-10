"""Daily job-posting refresh on Snowflake: load boards, build one fact, run its gates.

Parsing does no I/O and needs no credentials. preflight skips the whole run on any guard reason.
"""

import os
from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator, ShortCircuitOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback
from cosmos import ProfileConfig
from cosmos.constants import InvocationMode
from cosmos.operators.local import DbtBuildLocalOperator, DbtTestLocalOperator

from orchestration.sponsoratlas_orchestration.guard import (
    RUN_MINUTES,
    on_run_failure,
    preflight,
    record_usage,
)
from orchestration.sponsoratlas_orchestration.runtime import (
    DBT_ENV,
    DBT_VARS,
    JOB_GATES,
    ROOT,
    deadline_alert,
    freshness,
    load_boards,
    notify,
)

DBT_OPTIONS = {
    "project_dir": str(ROOT / "dbt"),
    "profile_config": ProfileConfig(
        profile_name="sponsoratlas",
        target_name=os.environ.get("DBT_TARGET", "snowflake"),
        profiles_yml_filepath=ROOT / "dbt/profiles.yml",
    ),
    "dbt_executable_path": str(ROOT / "orchestration/bin/dbt"),
    "invocation_mode": InvocationMode.SUBPROCESS,
    "indirect_selection": "cautious",
    "vars": DBT_VARS,
    "install_deps": False,
    "append_env": True,
    "partial_parse": False,
    "env": DBT_ENV,
}

with DAG(
    dag_id="sponsoratlas_daily",
    schedule="0 8 * * *",
    start_date=datetime(2026, 11, 6, tzinfo=UTC),
    end_date=datetime(2026, 11, 23, tzinfo=UTC),
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
    dagrun_timeout=timedelta(minutes=RUN_MINUTES["sponsoratlas_daily"]),
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
    boards = PythonOperator(task_id="load_boards", python_callable=load_boards)
    fresh = PythonOperator(task_id="observations_freshness", python_callable=freshness)
    fact = DbtBuildLocalOperator(
        task_id="dbt_job_fact",
        select=["fact_job_posting_daily"],
        emit_datasets=False,
        **DBT_OPTIONS,
    )
    gates = DbtTestLocalOperator(task_id="job_gates", select=JOB_GATES, **DBT_OPTIONS)
    usage = PythonOperator(task_id="record_usage", python_callable=record_usage)
    guard >> boards >> fresh >> fact >> gates >> usage
