#!/usr/bin/env bash
set -euo pipefail
web=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$web/scripts/env.sh"
export NEXT_TELEMETRY_DISABLED=1
unset NO_COLOR FORCE_COLOR
export NODE_OPTIONS=--max-old-space-size=2048
cd "$web/apps/site"
# Secure atomic exception: SWC refuses workspace ancestors writable by others.
export SWC_NATIVE_BINDING_CACHE
SWC_NATIVE_BINDING_CACHE=$(mktemp -d /tmp/sponsoratlas-task13-swc.XXXXXXXX)
chmod 700 "$SWC_NATIVE_BINDING_CACHE"
cleanup() {
  du -sb "$SWC_NATIVE_BINDING_CACHE"
  rm -rf -- "$SWC_NATIVE_BINDING_CACHE"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Private SWC cache: %s\n' "$SWC_NATIVE_BINDING_CACHE"
"$@"
