#!/usr/bin/env bash
# T14 frame-work measurement on the real worksite projection.
#   viz-frame-run.sh build             production build (CPU only, no GPU lock needed)
#   flock /var/tmp/rtx4090.lock viz-frame-run.sh measure <label>
# The measure step runs the two "pan zoom p95" tests (1x and 4x CPU) and writes
# evidence/viz-frame-<label>-{1,4}x.json in web/apps/site. Record nproc/uptime next to it.
set -euo pipefail
web=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
case "${1:-}" in
  build) exec bash "$web/scripts/site-command.sh" pnpm build ;;
  measure)
    label=${2:?label}
    [[ "$label" =~ ^[a-z0-9-]{1,40}$ ]] || { echo 'Invalid label' >&2; exit 2; }
    export VIZ_EVIDENCE=$label VIZ_GPU=${VIZ_GPU:-vulkan}
    exec bash "$web/scripts/site-command.sh" node ../../scripts/viz-browser-run.mjs 'pan zoom p95'
    ;;
  *) echo 'usage: viz-frame-run.sh build | measure <label>' >&2; exit 2 ;;
esac
