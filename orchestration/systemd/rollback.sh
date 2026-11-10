#!/usr/bin/env bash
# Stop the deployment, restore STOP and print the Snowflake cleanup. It never runs that cleanup.
set -uo pipefail

project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
status=0
for unit in \
  sponsoratlas-airflow-end.timer \
  sponsoratlas-airflow-scheduler.service \
  sponsoratlas-airflow-dag-processor.service \
  sponsoratlas-airflow-api-server.service; do
  systemctl --user disable --now "$unit" || status=1
done
touch "$project/.tools/orchestration/STOP" || status=1

cat <<NEXT

Units disabled and STOP restored. No Snowflake change was made; run these yourself if needed.

1. Remove the live rows, as AIRBYTE_SVC / LOADER on SPONSORATLAS_XS:

   DELETE FROM RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS WHERE replay_kind = 'LIVE_BOARD_FETCH';

2. Rebuild the incremental mart without them, as DBT_SVC / TRANSFORMER:

   cd $project
   set -a; . ~/.config/sponsoratlas/airflow.env; set +a
   DBT_TARGET_PATH=.tools/orchestration/dbt-target DBT_LOG_PATH=.tools/orchestration/dbt-logs \\
     orchestration/bin/dbt build --project-dir dbt --profiles-dir dbt --target snowflake \\
     --vars '{enable_private_applications: false, snapshot_schema: INTERMEDIATE}' \\
     --full-refresh --select fact_job_posting_daily+
NEXT
exit "$status"
