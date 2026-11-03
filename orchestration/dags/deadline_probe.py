"""Opt-in scheduler-only acceptance probe; never deployed by default."""

import os
import time
from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback

from orchestration.sponsoratlas_orchestration.runtime import deadline_alert, notify


def hold_past_deadline():
    time.sleep(25)


if os.environ.get("SPONSORATLAS_ENABLE_DEADLINE_PROBE") == "1":
    with DAG(
        dag_id="sponsoratlas_deadline_probe",
        schedule=None,
        start_date=datetime(2024, 10, 1, tzinfo=UTC),
        catchup=False,
        default_args={
            "retries": 2,
            "retry_delay": timedelta(seconds=1),
            "on_failure_callback": notify,
        },
        max_active_runs=1,
        deadline=DeadlineAlert(
            reference=DeadlineReference.DAGRUN_QUEUED_AT,
            interval=timedelta(seconds=5),
            callback=SyncCallback(deadline_alert),
        ),
        tags=["synthetic", "acceptance-probe"],
    ) as dag:
        PythonOperator(task_id="hold_past_runtime_deadline", python_callable=hold_past_deadline)
