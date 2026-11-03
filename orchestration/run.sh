#!/usr/bin/env bash
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project"
export PATH="$project/.tools/orchestration/venv/bin:$PATH"
export AIRFLOW_HOME="$project/.tools/orchestration/airflow"
export AIRFLOW__CORE__DAGS_FOLDER="$project/orchestration/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__PARALLELISM=2 AIRFLOW__DAG_PROCESSOR__PARSING_PROCESSES=1
export AIRFLOW__COSMOS__ENABLE_CACHE=False AIRFLOW__COSMOS__ENABLE_DAG_VERSIONING=False
export AIRFLOW__COSMOS__ENABLE_TELEMETRY=False DO_NOT_TRACK=1 SCARF_NO_ANALYTICS=1
export AIRFLOW__OPENLINEAGE__NAMESPACE=sponsoratlas
export PYTHONPATH="$project${PYTHONPATH:+:$PYTHONPATH}"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 DBT_SEND_ANONYMOUS_USAGE_STATS=false
export AIRFLOW__CORE__DEFAULT_TIMEZONE=utc
export AIRFLOW__API__HOST="${AIRFLOW__API__HOST:-127.0.0.1}"
export AIRFLOW__API__PORT="${AIRFLOW__API__PORT:-18780}"
export AIRFLOW__CORE__EXECUTION_API_SERVER_URL="${AIRFLOW__CORE__EXECUTION_API_SERVER_URL:-http://127.0.0.1:18780/execution/}"
export DBT_TARGET_PATH="$project/.tools/orchestration/dbt-target" DBT_LOG_PATH="$project/.tools/orchestration/dbt-logs"
# Only project-owned configuration/state/logs. No HOME override or globally scoped cleanup.
exec "$project/.tools/orchestration/venv/bin/${1:?airflow or pytest required}" "${@:2}"
