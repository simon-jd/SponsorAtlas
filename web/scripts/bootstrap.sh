#!/usr/bin/env bash
set -euo pipefail
WEB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$WEB_ROOT/../.tools/node-runtime/node-v24.22.1-linux-x64/bin:$PATH"
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
mkdir -p "$WEB_ROOT/.tools/pnpm"
cp "$WEB_ROOT/bootstrap/package.json" "$WEB_ROOT/bootstrap/package-lock.json" "$WEB_ROOT/.tools/pnpm/"
npm ci --prefix "$WEB_ROOT/.tools/pnpm" --ignore-scripts --no-audit --no-fund --cache "$WEB_ROOT/.cache/npm"
source "$WEB_ROOT/scripts/env.sh"
cd "$WEB_ROOT"
pnpm install --frozen-lockfile --ignore-scripts
