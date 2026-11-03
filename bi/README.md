# Reproduce BI evidence

These commands use the existing project Python/DuckDB and Node/Cube installations.
They do not publish or provision anything. Run from the repository root:

```sh
mkdir -p bi/.work
.tools/node-runtime/node-v24.22.1-linux-x64/bin/node bi/capture_cube.cjs
.venv/bin/python bi/reconcile.py --oracle-only
.venv/bin/python -m unittest discover -s bi/tests -v
.venv/bin/python bi/build_powerbi.py
.venv/bin/python bi/build_report.py
.venv/bin/python bi/verify_pbir.py
PYTHONPATH=bi/.work/hyper-sdk .venv/bin/python bi/build_tableau.py
PYTHONPATH=bi/.work/hyper-sdk .venv/bin/python bi/verify_extracts.py
.venv/bin/python bi/verify_tableau_source.py
.venv/bin/python bi/reconcile.py
```

The last command intentionally exits **1** until both real native exports exist.
`--oracle-only` means only Cube REST vs independent SQL passed. It never fills the
Power BI/Tableau columns. Do not run Hyper and Cube simultaneously. Scripts cap
source/temporary extract sizes and close their native processes. Generated
workbook source is not application-verified, even when its package/schema passes.

`capture_cube.cjs` verifies a derived manifest with checksum-identical symlinked
immutable Parquet in a disposable work directory. It captures fresh authenticated
REST requests and responses; credentials live only in process memory. It removes
its own scratch after stopping Cube and preserves upstream snapshots/evidence.

The isolated Hyper SDK is deliberately not a root dependency. The exact official
PyPI URL/version/wheel SHA/bytes/expanded bytes/license are in
`evidence/hyper-dependency.json`. To restore it, download that one wheel to
`bi/.work`, verify its SHA256 and expanded total <=700MB, check ZIP paths, unpack
to `bi/.work/hyper-sdk`, set its `tableauhyperapi/bin/hyper/hyperd` user executable
bit, and delete the wheel. Reuse root cffi/pycparser; do not install a second data
stack. This task's total added peak must remain <1GB. Runtime telemetry is off,
memory_limit=1G, CPU affinity=2, logs capped to two 1MB files, and extraction runs
in 10,000-row chunks. `evidence/hyper-extract.json` identifies actual imports.
The SDK is Apache 2.0; [official Linux support and requirements](https://tableau.github.io/hyper-db/docs/installation/).

`verify_pbir.py` downloads only bounded Microsoft JSON schemas into ignored local
cache (or uses that cache) and validates report source, not TMDL or engine results.
`verify_extracts.py` reopens the actual Hyper files from TWBX, compares every field
in all rows against the manifest-protected Parquet, and checks no extract mutation.
No SQL result is passed to the native-results importer as a dashboard result.

`build_tableau.py --source-only` updates the workbook XML in TWBX while preserving
verified extract bytes; no Hyper process runs. `verify_tableau_source.py` checks
Tableau's official 2022.4 XSD through existing libxml2, plus exact filter/slice/card
references, four cross-sheet action mappings/reset/exclusions, and the matched
comparison dashboard/shared parameter dependencies/no-private-filter contract. Its two warnings
are explicitly identified public-schema gaps documented by Tableau's own validator,
not ignored arbitrary errors. This remains source validation, not interaction QA.

## Source-only comparison regression

After edits to comparison sources, reuse the existing verified Hyper files and
cached schemas; no Cube or Hyper service needs to start:

```sh
.venv/bin/python bi/build_powerbi.py
.venv/bin/python bi/build_report.py
PYTHONPATH=bi/.work/hyper-sdk .venv/bin/python bi/build_tableau.py --source-only
.venv/bin/python -m unittest discover -s bi/tests -p 'test_*comparison*.py' -v
.venv/bin/python -m unittest discover -s bi/tests -p 'test_tableau_source.py' -v
.venv/bin/python bi/verify_pbir.py
.venv/bin/python bi/verify_tableau_source.py
.venv/bin/python bi/verify_comparison.py
```

Schema-only generators and the bounded comparison SQL check current readonly mode,
file sizes and manifest identity, reusing prior full hash/grain verification. They
do not refresh the original source/full-row verification evidence. TWB repacking
checks original extract hashes and exact inner/outer XML equivalence. The local
SQL scope results in `evidence/comparison-scope.json` are not native engine results.
`powerbi/comparison-checks.dax` is the pending actual-app follow-up for national,
CBSA 35620, FY 2025 and unsupported state scopes. Tableau's matched comparison tab
uses four shared editable parameter controls; set the same scenarios there and
verify every displayed value and scope label in the native app. Default five-number
native reconciliation remains FAIL until authentic native evidence is supplied.

## Supplying authentic native results

After following [native completion](dashboard-spec.md#source-lifecycle-and-native-acceptance),
place original native exports and screenshots under ignored `bi/native-results/`.
For each `powerbi_dax` / `tableau` engine, provide `<engine>.json` with:

```json
{
  "engine": "powerbi_dax",
  "application_version": "ACTUAL VERSION FROM THE APP",
  "executed_at": "ACTUAL UTC EXECUTION TIME",
  "manifest_sha256": "SHA256 OF THE EXACT ORIGINAL SNAPSHOT MANIFEST",
  "queries_sha256": "SHA256 OF bi/queries.json",
  "artifact_sha256": "SHA256 OF SAVED NATIVE PBIX OR TWBX",
  "export_file": "powerbi-original-export.csv",
  "export_file_sha256": "SHA256",
  "screenshot_file": "powerbi-native-render.jpg",
  "screenshot_file_sha256": "SHA256"
}
```

The export CSV must have `metric,value` and exactly the five metric IDs from
`queries.json`; empty value denotes executed NULL. In DAX use the supplied query
without report filters. In Tableau export each actual KPI result at full precision
and retain the original underlying/crosstab exports alongside the five-row CSV.
Metadata is a human execution attestation, not cryptographic proof of an engine;
an independent reviewer must inspect the app execution record, artifacts and
screenshots. Never manufacture these fields from SQL or source formulas.

`reconcile.sql` then joins all four evidence origins with NULL-aware comparisons
and tolerance 1e-8 before presentation rounding. Native app-open/render review,
Snowflake source execution and the prohibited Public link remain separate gates.
