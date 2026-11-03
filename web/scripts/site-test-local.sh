#!/usr/bin/env bash
# Full local gate; no external service, global install, or browser download.
set -euo pipefail
web=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$web/scripts/env.sh"
cd "$web/apps/site"
mkdir -p evidence
python3 ../../scripts/run-monitored.py site-unit-final env TZ=Pacific/Honolulu node --test tests/format.test.ts tests/data.test.ts tests/cube.test.ts
python3 ../../scripts/run-monitored.py site-build-final bash ../../scripts/site-command.sh pnpm build
python3 ../../scripts/run-monitored.py site-fixture-final bash ../../scripts/site-command.sh node ../../scripts/site-browser-run.mjs fixture
python3 ../../scripts/run-monitored.py site-actual-final bash ../../scripts/site-command.sh node ../../scripts/site-browser-run.mjs actual
