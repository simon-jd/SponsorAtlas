# Evidence

Raw outputs (JSON, logs, SVG) behind the results table in the [top-level README](../../README.md).
Every run was local, on one Linux machine (32 cores, shared with other workloads).

| Directory | Contents |
|---|---|
| [`task03-uscis-release-20261029/`](task03-uscis-release-20261029/) | full-year staging build: dbt log and `run_results.json`, gate shares, RAW → staging → released reconciliation per file, USCIS blank-address exclusion |
| [`task09-backfill/`](task09-backfill/) | FY2025 backfill through the Airflow DAG (2026-10-28): per-step timing JSON and logs, fact summary; the backfill DAG writes here |
| [`task04-er-threshold-0875/`](task04-er-threshold-0875/) | 2026-10-29 rebuild from ER export to facts: ER input, training and prediction summaries, dbt logs, downstream counts, Cube and map re-export logs |
| [`task04-human-acceptance-20261029/`](task04-human-acceptance-20261029/) | ER evaluation against the 559 human labels: holdout report, training and prediction summaries, logs |
| [`er-registry-archive-20261029/`](er-registry-archive-20261029/) | employer-ID stability check (prediction inheriting the archived registry vs the canonical run) |
| [`task14-fullyear-map/`](task14-fullyear-map/) | full-year map projection manifest, H3 cells per scope, server cost, frame-cost measurements, browser E2E logs |
| [`task08-lineage-fy2025/`](task08-lineage-fy2025/) | lineage exported from the local OpenMetadata API (raw responses, SVG), loader bridge and chain verification |
| [`task02-cdc/`](task02-cdc/) | local CDC drill output and the archived-file row-count reconciliation |

Older and superseded runs are not part of the published repository.
