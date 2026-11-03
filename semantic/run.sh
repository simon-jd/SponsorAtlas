#!/usr/bin/env bash
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project"
export PATH="$project/.tools/node-runtime/node-v24.22.1-linux-x64/bin:$PATH"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
[[ $(node --version) == v24.22.1 ]] || { echo 'Project Node 24.22.1 required' >&2; exit 1; }
command=${1:-test}
kind=${2:-fixture}
case $command in
  install) cd semantic; npm ci --cache "$project/.tools/npm-cache"; cd "$project"; .venv/bin/python semantic/scripts/install_native.py ;;
  prepare|test)
    [[ $kind == fixture || $kind == actual ]] || { echo 'kind must be fixture or actual' >&2; exit 1; }
    .venv/bin/python -m semantic.scripts.prepare "$kind"
    if [[ $command == test ]]; then
      export SEMANTIC_SOURCE=parquet SEMANTIC_KIND="$kind"
      cd semantic
      node --max-old-space-size=512 --test --test-concurrency=1 tests/semantic.test.ts
      cd "$project"
      .venv/bin/python -m semantic.scripts.inspect_preagg "$kind"
    fi
    ;;
  serve)
    [[ ${2:-} == parquet || ${2:-} == snowflake ]] || { echo 'serve requires explicit parquet or snowflake source' >&2; exit 1; }
    export SEMANTIC_SOURCE="$2"
    if [[ $2 == parquet ]]; then
      [[ ${3:-actual} == actual || ${3:-actual} == fixture ]] || { echo 'Local kind must be actual or fixture; exported snapshots use SEMANTIC_SNAPSHOT_DIR' >&2; exit 1; }
      export SEMANTIC_WORK="${SEMANTIC_SNAPSHOT_DIR:-$project/.tools/semantic/${3:-actual}}"
    else
      export SEMANTIC_WORK="$project/.tools/semantic/snowflake"
    fi
    : "${CUBEJS_API_SECRET:?Set server-side CUBEJS_API_SECRET}"
    cd semantic; exec node --max-old-space-size=1024 server.cjs
    ;;
  test-source)
    cd semantic
    node --max-old-space-size=768 --test --test-concurrency=1 --test-force-exit tests/snowflake.test.ts tests/snowflake-lifecycle.test.ts tests/verification.test.ts tests/external-preagg.test.ts tests/fallback.test.ts
    ;;
  verify-snowflake)
    export SEMANTIC_SOURCE=snowflake
    exec node --max-old-space-size=1024 semantic/scripts/verify_snowflake.cjs
    ;;
  export-snowflake)
    export SEMANTIC_SOURCE=snowflake
    exec .venv/bin/python -m semantic.scripts.export_snowflake "${2:?A unique snapshot name is required}"
    ;;
  clean)
    # Only task-owned generated scratch; immutable sources and evidence are preserved.
    .venv/bin/python - <<'PY'
from pathlib import Path
import shutil
for kind in ('fixture', 'actual', 'readonly-external', 'snowflake'):
    for directory in ('scratch', 'external-scratch'):
        path = Path('.tools/semantic') / kind / directory
        if path.exists():
            shutil.rmtree(path)
            print('Removed', path)
PY
    ;;
  *) echo 'Usage: semantic/run.sh {install|prepare|test|test-source|serve|verify-snowflake|export-snowflake|clean} ...' >&2; exit 1 ;;
esac
