#!/usr/bin/env bash
# End of the deployment, fired by sponsoratlas-airflow-end.timer: export run evidence, append the
# final alert, touch STOP, disable the units. Safe to rerun. A failed export or alert never
# prevents the shutdown; it makes the script exit non-zero after the shutdown is done.
set -uo pipefail

project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
run="$project/orchestration/run.sh"
env_file="$HOME/.config/sponsoratlas/airflow.env"
stop="$project/.tools/orchestration/STOP"
alerted="$project/.tools/orchestration/END_ALERT_SENT"
units=(
  sponsoratlas-airflow-api-server.service
  sponsoratlas-airflow-dag-processor.service
  sponsoratlas-airflow-scheduler.service
  sponsoratlas-airflow-end.timer
)
alert='from orchestration.sponsoratlas_orchestration.runtime import notify; notify({"dag_id": "sponsoratlas"}, event="deployment_end", detail="end timer stopped the deployment")'

dry_run=false
case "${1:-}" in
  "") ;;
  --dry-run) dry_run=true ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

status=0
act() {
  echo "+ $*"
  "$dry_run" && return 0
  "$@" && return 0
  local code=$?
  echo "FAILED with exit $code: $1" >&2
  status=1
  return "$code"
}

if ! "$dry_run" && [[ -r $env_file ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
fi

# Bounded, so that nothing here can keep the deployment running.
act timeout 900 "$run" python "$project/orchestration/bin/export_runs.py"
if [[ -e $alerted ]]; then
  echo "final alert already sent"
elif act timeout 120 "$run" python -c "$alert"; then
  act touch "$alerted"
fi
act touch "$stop"
for unit in "${units[@]}"; do
  act systemctl --user disable --now "$unit"
done
exit "$status"
