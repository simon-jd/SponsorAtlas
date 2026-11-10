#!/usr/bin/env bash
# Install the deployment's user units and environment file, and migrate the metadata database.
# Never removes STOP. Enables and unpauses nothing unless asked:
#   --enable         enable the end timer and the three services; STOP still keeps them down
#   --unpause DAG    unpause a DAG, after the DAG processor has parsed it (repeatable)
set -euo pipefail

enable=false
unpause=()
while (($#)); do
  case "$1" in
    --enable) enable=true ;;
    --unpause) shift; unpause+=("${1:?--unpause needs a DAG id}") ;;
    *) echo "usage: $0 [--enable] [--unpause DAG]..." >&2; exit 2 ;;
  esac
  shift
done

project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
here="$project/orchestration/systemd"
if [[ $project != "$HOME/Desktop/SponsorAtlas" ]]; then
  echo "The units name %h/Desktop/SponsorAtlas, but this checkout is $project" >&2
  exit 1
fi
unit_dir="$HOME/.config/systemd/user"
env_dir="$HOME/.config/sponsoratlas"
env_file="$env_dir/airflow.env"
services=(
  sponsoratlas-airflow-api-server.service
  sponsoratlas-airflow-dag-processor.service
  sponsoratlas-airflow-scheduler.service
)

mkdir -p "$unit_dir"
install -m 0644 "$here"/*.service "$here"/*.timer "$unit_dir/"
systemctl --user daemon-reload

install -d -m 0700 "$env_dir"
if [[ ! -e $env_file ]]; then
  account=${SNOWFLAKE_ACCOUNT:?set SNOWFLAKE_ACCOUNT=orgname-accountname to create $env_file}
  (
    umask 077
    while IFS= read -r line || [[ -n $line ]]; do
      line=${line//@HOME@/$HOME}
      line=${line//@SNOWFLAKE_ACCOUNT@/$account}
      printf '%s\n' "${line//@PROJECT@/$project}"
    done <"$here/airflow.env.template" >"$env_file"
  )
  echo "Created $env_file"
fi
chmod 600 "$env_file"

# Only shell builtins handle the secret, so it never appears in a process's arguments.
current=$(sed -n 's/^CUBEJS_API_SECRET=//p' "$env_file" | tail -n 1)
if [[ -z $current || $current == "@GENERATED_BY_INSTALL@" ]]; then
  secret=$(openssl rand -hex 32)
  partial=$(mktemp "$env_file.XXXXXX")
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line == CUBEJS_API_SECRET=* ]] || printf '%s\n' "$line"
  done <"$env_file" >"$partial"
  printf 'CUBEJS_API_SECRET=%s\n' "$secret" >>"$partial"
  mv "$partial" "$env_file"
  echo "Generated CUBEJS_API_SECRET in $env_file"
fi

"$project/orchestration/run.sh" airflow db migrate

if "$enable"; then
  # A new deployment gets its own final alert.
  rm -f "$project/.tools/orchestration/END_ALERT_SENT"
  systemctl --user enable --now sponsoratlas-airflow-end.timer
  systemctl --user enable --now "${services[@]}"
fi
for dag in ${unpause[@]+"${unpause[@]}"}; do
  "$project/orchestration/run.sh" airflow dags unpause --yes "$dag"
done

cat <<NEXT

Installed. STOP is untouched: $project/.tools/orchestration/STOP
Next steps:
  1. Review $env_file
  2. $here/install.sh --enable
  3. rm $project/.tools/orchestration/STOP
  4. systemctl --user start ${services[*]}
  5. $here/install.sh --unpause sponsoratlas_daily --unpause sponsoratlas
Verification backfill (schedule=None; it runs only when triggered):
  $here/install.sh --unpause sponsoratlas_backfill
  $project/orchestration/run.sh airflow dags trigger sponsoratlas_backfill --conf '{"fiscal_year": 2025, "label": "verify-YYYYMMDD"}'
To stop early: $here/rollback.sh
NEXT
