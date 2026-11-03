#!/usr/bin/env bash
# All runtime artifacts and telemetry settings remain within SponsorAtlas.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export DBT_SEND_ANONYMOUS_USAGE_STATS=false
export DBT_LOG_PATH="${DBT_LOG_PATH:-$project_root/.tools/staging/logs}"
export DBT_TARGET_PATH="${DBT_TARGET_PATH:-$project_root/.tools/staging/target}"
export SPONSORATLAS_DUCKDB_PATH="${SPONSORATLAS_DUCKDB_PATH:-$project_root/.tools/staging/sponsoratlas.duckdb}"
mkdir -p "$DBT_LOG_PATH" "$DBT_TARGET_PATH"
exec "$project_root/.venv/bin/dbt" "$@" --project-dir "$project_root/dbt" --profiles-dir "$project_root/dbt"
