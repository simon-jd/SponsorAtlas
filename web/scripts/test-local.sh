#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$WEB_ROOT"
# SWC refuses caches beneath replaceable ancestors in this sandbox. A private,
# atomically created sticky-/tmp child preserves its validation without HOME writes.
SWC_TASK_CACHE=$(mktemp -d /tmp/sponsoratlas-task12-swc.XXXXXX)
chmod 700 "$SWC_TASK_CACHE"
export SWC_NATIVE_BINDING_CACHE="$SWC_TASK_CACHE"
server_pid=''
cleanup() {
  if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi
  rm -rf -- "$SWC_TASK_CACHE"
}
trap cleanup EXIT
pnpm -F ui tokens
pnpm -F ui test-tokens
pnpm -F ui typecheck
pnpm -F ui build-storybook
node scripts/serve-storybook.mjs &
server_pid=$!
EXPECTED_SERVER_PID="$server_pid" node -e 'for(let i=0;i<40;i++){try{const response=await fetch("http://127.0.0.1:6006/index.json");if(response.ok&&response.headers.get("x-sponsoratlas-test-server-pid")===process.env.EXPECTED_SERVER_PID)process.exit(0)}catch{}await new Promise(r=>setTimeout(r,100));}process.exit(1);'
# Ensure the server we started owns the port, rather than testing a stale instance.
kill -0 "$server_pid"
pnpm -F ui test-storybook
