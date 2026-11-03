#!/usr/bin/env bash
set -euo pipefail
web=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$web/scripts/env.sh"
quality_run_id=${QUALITY_RUN_ID:-}
if [[ -n "$quality_run_id" && ! "$quality_run_id" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
  echo 'Invalid QUALITY_RUN_ID' >&2
  exit 2
fi
monitor_label=quality-ci
if [[ -n "$quality_run_id" ]]; then monitor_label="quality-ci-$quality_run_id"; fi
node "$web/scripts/quality-environment.mjs"
cd "$web/apps/site"
mkdir -p evidence
python3 ../../scripts/run-monitored.py "$monitor_label-units" pnpm test:unit
python3 ../../scripts/run-monitored.py "$monitor_label-build" bash ../../scripts/site-command.sh pnpm build
if [[ -n "$quality_run_id" ]]; then
  accounting_dir="$PWD/evidence/quality-runs/$quality_run_id/accounting"
  QUALITY_EVIDENCE_DIRECTORY="$accounting_dir" python3 ../../scripts/run-monitored.py "$monitor_label-accounting" bash ../../scripts/site-command.sh pnpm exec playwright test --config quality.playwright.config.ts --grep accounting --output "$accounting_dir/test-results"
else
  python3 ../../scripts/run-monitored.py "$monitor_label-accounting" bash ../../scripts/site-command.sh pnpm exec playwright test --config quality.playwright.config.ts --grep accounting
fi
python3 ../../scripts/run-monitored.py "$monitor_label-smoke" bash ../../scripts/site-command.sh node scripts/quality-run.mjs fixture smoke tests/quality/context-retry.spec.ts tests/quality/viz-retry.spec.ts tests/quality/quality-entry.spec.ts --retries=0
python3 ../../scripts/run-monitored.py "$monitor_label-budget" bash ../../scripts/site-command.sh node scripts/quality-run.mjs fixture budget
python3 ../../scripts/run-monitored.py "$monitor_label-visual" bash ../../scripts/site-command.sh node scripts/quality-run.mjs fixture visual --update-snapshots=none
python3 ../../scripts/run-monitored.py "$monitor_label-rum" bash ../../scripts/site-command.sh node scripts/quality-run.mjs fixture rum
python3 ../../scripts/run-monitored.py "$monitor_label-misconfiguration" bash ../../scripts/site-command.sh node scripts/quality-run.mjs fixture misconfiguration
for locale in en zh; do
  for theme in light dark; do
    python3 ../../scripts/run-monitored.py "$monitor_label-lhci-$locale-$theme" bash ../../scripts/site-command.sh node scripts/quality-run.mjs fixture lhci "$locale" "$theme"
  done
done
