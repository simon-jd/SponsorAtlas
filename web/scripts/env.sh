#!/usr/bin/env bash
# Source from web/ or use: source scripts/env.sh
WEB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$WEB_ROOT/.tools/pnpm/node_modules/@pnpm/exe.linux-x64:$WEB_ROOT/../.tools/node-runtime/node-v24.22.1-linux-x64/bin:$PATH"
export npm_config_cache="$WEB_ROOT/.cache/npm"
export XDG_CACHE_HOME="$WEB_ROOT/.cache/xdg"
export XDG_CONFIG_HOME="$WEB_ROOT/.cache/config"
export XDG_DATA_HOME="$WEB_ROOT/.cache/data"
export PNPM_HOME="$WEB_ROOT/.tools/pnpm"
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
export PLAYWRIGHT_BROWSERS_PATH="$WEB_ROOT/.cache/unused-browsers"
export STORYBOOK_DISABLE_TELEMETRY=1
export TMPDIR="$WEB_ROOT/.cache/tmp"
mkdir -p "$TMPDIR"
export XDG_STATE_HOME="$WEB_ROOT/.cache/state"
export XDG_RUNTIME_DIR="$WEB_ROOT/.cache/runtime"
export PNPM_CONFIG_STATE_DIR="$WEB_ROOT/.cache/pnpm-state"
export PNPM_CONFIG_CONFIG_DIR="$WEB_ROOT/.cache/pnpm-config"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
