#!/usr/bin/env bash
# Plain-text run status. Reads state only; starts nothing and writes no evidence.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
exec "$project/orchestration/run.sh" python "$project/orchestration/bin/export_runs.py" --status "$@"
