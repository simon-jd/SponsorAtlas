# Evidence

Raw outputs (JSON, logs, SVG) behind the results table in the [top-level README](../../README.md).
Every run started from one Linux machine (32 cores, shared with other workloads), except the Tableau
Public app steps (load and publish) in `task11-tableau-public-20261105/`, which ran on a Mac. Runs were
local on DuckDB, except the `*snowflake*` directories, which ran against a Snowflake trial account, and
`task11-tableau-public-20261105/`, which covers the Tableau Public app and the published workbook.

| Directory | Contents |
|---|---|
| [`task03-uscis-release-20261029/`](task03-uscis-release-20261029/) | full-year staging build: dbt log and `run_results.json`, gate shares, RAW → staging → released reconciliation per file, USCIS blank-address exclusion |
| [`task09-backfill/`](task09-backfill/) | FY2025 backfill through the Airflow DAG (2026-10-28): per-step timing JSON and logs, fact summary; backfill DAG runs with `overwrite_canonical` write here, verification runs write to `task09-backfill-airflow-<label>/` |
| [`task09-backfill-airflow-verify-20261106/`](task09-backfill-airflow-verify-20261106/) | FY2025 backfill DAG verification run on 2026-11-06 (label `verify-20261106`), local DuckDB in separate databases: per-step timing JSON and logs; all 10 steps succeeded (215.8 s summed) |
| [`task09-airflow-snowflake-20261106/`](task09-airflow-snowflake-20261106/) | Airflow DAG runs on the Snowflake trial since 2026-11-06. `runs/`: manifests of the 8 runs counted as evidence (scheduled daily runs 2026-11-06 to 2026-11-10, of which the 2026-11-06 one reused an archive fetched at 05:26 UTC; the scheduled weekly run of 2026-11-08; the manual reruns `manual_first_snowflake_20261106_r2` and `manual_full_snowflake_20261106_r2`), each with mode, dbt target, versions, board-load totals (daily DAG), the ledger's credit estimate and the whole-account metered total (other projects included, lags by hours, not a per-run figure). `full-run-node-results/`: dbt node results of `manual_full_snowflake_20261106_r2` (180 results, 0 failures). `failed-attempts/`: the errors of the two manual runs that failed on 2026-11-06. `dag-runs.json`: exported 2026-11-10 20:25 UTC, 16 runs, 8 counted as evidence under the rule in the file (it does not record archive reuse) |
| [`task04-er-threshold-0875/`](task04-er-threshold-0875/) | 2026-10-29 rebuild from ER export to facts: ER input, training and prediction summaries, dbt logs, downstream counts, Cube and map re-export logs |
| [`task04-human-acceptance-20261029/`](task04-human-acceptance-20261029/) | ER evaluation against the 559 human labels: holdout report, training and prediction summaries, logs |
| [`er-registry-archive-20261029/`](er-registry-archive-20261029/) | employer-ID stability check (prediction inheriting the archived registry vs the canonical run) |
| [`task14-fullyear-map/`](task14-fullyear-map/) | full-year map projection manifest, H3 cells per scope, server cost, frame-cost measurements, browser E2E logs |
| [`task08-lineage-fy2025/`](task08-lineage-fy2025/) | lineage exported from the local OpenMetadata API (raw responses, SVG), loader bridge and chain verification |
| [`task02-cdc/`](task02-cdc/) | local CDC drill output and the archived-file row-count reconciliation |
| [`task01-snowflake-grants-20261031/`](task01-snowflake-grants-20261031/) | grant, marts-access and account-default tests on the trial account: 8/8 (7 live, 1 offline); policy references on the real build |
| [`task02-snowflake-raw-20261030/`](task02-snowflake-raw-20261030/) | RAW loaded by script, not Airbyte: per-table summary, per-file reconciliation, `make test` log |
| [`task06-snowflake-dbt-20261030/`](task06-snowflake-dbt-20261030/) | dbt on Snowflake: four `run_results.json` files and logs, checksum comparison with the local DuckDB build, `make test` log |
| [`task07-cube-snowflake-20261031/`](task07-cube-snowflake-20261031/) | Cube REST on Snowflake: run log with the page-bound catch, verification output, Parquet export from the marts, rate forms, `make test` log |
| [`task08-snowflake-policies-20261031/`](task08-snowflake-policies-20261031/) | policy tests on probe tables: 5/5 (3 live, 2 static) |
| [`task10-snowflake-cost-20261031/`](task10-snowflake-cost-20261031/) | one cost experiment on an 8-partition clone, its reconciliation 8 h later, top-20 report, resource monitor readings |
| [`task11-tableau-public-20261105/`](task11-tableau-public-20261105/) | load failures of generated `bi/public` workbook variants, then the 2026-11-06 load of the full workbook in the app (log signals and the app's SQL re-run on the extracts; no screen read); the 2026-11-10 publication is in `published-20261110/` (next row) |
| [`task11-tableau-public-20261105/published-20261110/`](task11-tableau-public-20261105/published-20261110/) | the `bi/public` workbook published on Tableau Public on 2026-11-10, a static snapshot (`20261106T052440Z-aa007296`) exported from the Snowflake public marts on 2026-11-06 before any scheduled run: `reconcile_public.py` output for the published Reconciliation sheet (`result.json`, 5/5 PASS; `sheet.csv`; `sheet.png`) and `published.json` (workbook settings, sheet CSV comparison with DuckDB over the snapshot, extract hashes, private-name scan counts only) |
| [`test-run-20261110/`](test-run-20261110/) | `make test` and `make check` on 2026-11-10, commit 82aaae3 plus the changes committed with this folder, offline suites only: both exit 0; 931 passed, 12 skipped (live Snowflake, ER acceptance and Spark Connect opt-ins); run times and host load in `run-context.txt` |
| [`task01-snowflake-grants-20261030/`](task01-snowflake-grants-20261030/), [`task10-snowflake-cost-20261030/`](task10-snowflake-cost-20261030/) | earlier Snowflake runs: grant tests (6 passed), query history and monitor readings |

Apart from those two earlier Snowflake directories, older and superseded runs are not part of the
published repository.
