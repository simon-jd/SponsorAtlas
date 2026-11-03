"""Manual fiscal-year backfill: rebuild the local pipeline from the archived LCA quarters.

Trigger with conf {"fiscal_year": 2025}. Each task runs one step of
`backfill.backfill_steps` as a subprocess and writes its timing JSON and log under
docs/evidence/task09-backfill/. Parsing does no I/O.
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG

from orchestration.sponsoratlas_orchestration.backfill import backfill_steps, run_one
from orchestration.sponsoratlas_orchestration.runtime import ROOT, notify

EVIDENCE = str(ROOT / "docs/evidence/task09-backfill")
YEAR = "{{ dag_run.conf.get('fiscal_year', 2025) }}"

with DAG(
    dag_id="sponsoratlas_backfill",
    schedule=None,
    start_date=datetime(2024, 10, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        # Every step refuses to overwrite its destination; a retry would fail the same way.
        "retries": 0,
        "execution_timeout": timedelta(hours=2),
        "on_failure_callback": notify,
    },
    on_failure_callback=notify,
    render_template_as_native_obj=True,
    tags=["sponsoratlas", "backfill"],
) as dag:
    previous = None
    for step in backfill_steps(2025):
        task = PythonOperator(
            task_id=step["name"],
            python_callable=run_one,
            op_kwargs={"year": YEAR, "name": step["name"], "evidence": EVIDENCE},
        )
        if previous is not None:
            previous >> task
        previous = task
