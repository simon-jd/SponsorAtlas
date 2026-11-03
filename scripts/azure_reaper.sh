#!/usr/bin/env bash
# Default: preview expired resources. --apply deletes; --down targets the fixed group.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$project_root/.venv/bin/python" ]]; then
    python_bin="$project_root/.venv/bin/python"
else
    python_bin="python3"
fi
exec "$python_bin" "$project_root/scripts/azure_resources.py" "$@"
