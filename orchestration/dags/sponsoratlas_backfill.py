"""Manual fiscal-year backfill: rebuild the local pipeline from the archived LCA quarters.

Each task runs one step of `backfill.backfill_steps` as a subprocess. Parsing does no I/O.

Conf {"fiscal_year": 2025, "label": "verify-20261106"} is a verification run: it writes only
to new label directories (`backfill.labelled_paths`) and docs/evidence/task09-backfill-airflow-
<label>/, and inherits the canonical employer registry read only.

Conf {"fiscal_year": 2025, "overwrite_canonical": true} rewrites the canonical databases and
docs/evidence/task09-backfill/. Any other conf, including no label at all, is refused.
"""

from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG

from orchestration.sponsoratlas_orchestration.backfill import backfill_steps, run_one
from orchestration.sponsoratlas_orchestration.runtime import ROOT, notify

EVIDENCE = str(ROOT / "docs/evidence/task09-backfill")
YEAR = "{{ dag_run.conf.get('fiscal_year', 2025) }}"
LABEL = "{{ dag_run.conf.get('label') }}"
OVERWRITE = "{{ dag_run.conf.get('overwrite_canonical', False) }}"

with DAG(
    dag_id="sponsoratlas_backfill",
    schedule=None,
    start_date=datetime(2024, 10, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        # Only the loaders refuse an existing destination; dbt steps rebuild in place. A failed
        # step needs a person to look at what it left behind, not an automatic retry.
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
            op_kwargs={
                "year": YEAR,
                "name": step["name"],
                "evidence": EVIDENCE,
                "label": LABEL,
                "overwrite_canonical": OVERWRITE,
            },
        )
        if previous is not None:
            previous >> task
        previous = task
