# SponsorAtlas

SponsorAtlas is a data platform and website about US employers that file H-1B Labor Condition
Applications (LCAs). It takes the Department of Labor's quarterly LCA disclosure files and USCIS
H-1B Employer Data Hub files, archives each official file by SHA-256, models them with dbt,
resolves the many spellings of one employer into a single stable employer ID, and serves the
result through a Cube semantic layer to a bilingual (English/Chinese) Next.js site with employer
search, comparison and an H3 map of worksites and wages. The goal is to answer, from official data
and with every number traceable to a source file, which employers file for H-1B workers, where,
and at what wages. Entity resolution, the site and the map ran locally on one machine, on DuckDB.
The dbt build and Cube also ran on a Snowflake trial account, by hand from 2026-10-30 to 2026-11-01
and from Airflow since 2026-11-06 (the full build and a Cube refresh by hand on 2026-11-06 and on
the weekly schedule on 2026-11-08; the job-posting fact daily; both schedules end in November 2026),
on script-loaded RAW that includes exports of the local ER output, so Snowflake is not
independent of the local build (see Results). A static snapshot of the Snowflake marts, exported on
2026-11-06 before any scheduled run, was published on
[Tableau Public](https://public.tableau.com/app/profile/simon.d5578/viz/SponsorAtlas-public/SponsorAtlaspublicsnapshot)
on 2026-11-10 and reconciled 5/5 against the snapshot; it does not refresh. Databricks and Azure
exist as code only and were not run. An LCA is not a visa approval and does not mean that a
particular job offers sponsorship.

![Map page on the full-year data](docs/images/map-fy2025.png)

*Map page on the full-year data (local build, 2026-10-28). Cells are counts of LCA cases at
Census 2020 ZIP-area or place reference points, not worksite addresses.*

## Data flow

```text
 DOL LCA disclosure files    USCIS H-1B Employer Data Hub    BLS OEWS, Census, SOC references
            └──────────────────────────┬───────────────────────────────┘
               scripts.archive_sources: download, pin SHA-256, keep provenance (data/raw)
                                       ▼
 RAW (DuckDB) ──► dbt staging: per-release column maps, annual wages, per-fiscal-year gates
                                       │
                                       ├──► er/: Splink 5 (u/EM) on DuckDB + constrained clustering;
                                       │         employer registry archived by SHA-256 → stable IDs
                                       ▼
                 dbt marts: SCD2 employer snapshot, 4 dimensions, 3 facts with enforced contracts
                                       │
          ┌────────────────────────────┼─────────────────────────────┐
          ▼                            ▼                             ▼
  Cube 1.8 REST over a        worksite projection →          OpenMetadata 2.0.6 (local):
  Parquet export              H3 aggregation (h3-js)         column lineage RAW → marts
          └──────────────┬─────────────┘
                         ▼
   Next.js 16 site (en/zh): employer search, detail, compare, deck.gl H3 map with SVG fallback

 Orchestration: Airflow 3 + Cosmos on one home machine (LocalExecutor, SQLite, systemd --user units: a
 development deployment). Since 2026-11-06 a daily DAG loads public job-board observations into Snowflake
 RAW and builds the job-posting fact and its gates; a weekly DAG runs the full dbt build, quality gates and
 a Cube refresh on Snowflake, over the same script-loaded RAW (no new LCA, USCIS or BLS data). Alerts go
 to a local log only. An earlier daily DAG was rehearsed on a synthetic source on 2026-10-24. Manual
 fiscal-year backfill DAG (local DuckDB)
 Snowflake trial: RAW loaded by script → the same dbt project → Cube REST verification; the site and map do not read it
 Tableau Public (bi/public): static snapshot of the Snowflake marts exported 2026-11-06, published 2026-11-10,
 reconciled 5/5; not connected to Snowflake and not refreshed:
 https://public.tableau.com/app/profile/simon.d5578/viz/SponsorAtlas-public/SponsorAtlaspublicsnapshot
```

## Results

Numbers come from local runs on one Linux machine (32 cores, shared with other workloads), except
the rows that start with Snowflake, which that machine ran against a Snowflake trial account on the
X-Small warehouse `SPONSORATLAS_XS`. Each row links the raw output it was read from.

| Result | Scope | Evidence |
|---|---|---|
| 717,449 LCA rows from five official files (FY2024 Q4 120,897; FY2025 Q1 107,414, Q2 132,133, Q3 238,425, Q4 118,580); RAW and staging row counts are equal for every file | local DuckDB, files pinned by SHA-256 | [summary.json](docs/evidence/task03-uscis-release-20261029/summary.json) |
| Full-year staging build exits 0: 38 tests pass (37 data tests, 1 unit test), 7 models build. LCA FY2025 rows without a county: 6,716 of 596,552 (1.13%, limit 5%) | `make staging-actual`, 2026-10-29 | [build.log](docs/evidence/task03-uscis-release-20261029/build.log), [summary.json](docs/evidence/task03-uscis-release-20261029/summary.json) |
| USCIS rows with no state, city or ZIP are kept in staging but not released: FY2023 33,332 = 30,857 released + 2,475 excluded; FY2022 59,983 = 59,976 + 7. With them, FY2023 would fail the 5% state gate (7.44%) | same run | [end-to-end.json](docs/evidence/task03-uscis-release-20261029/end-to-end.json) |
| 717,449 LCA input rows → 713,467 cases (latest status per case number; FY2024 Q4 118,646, FY2025 594,821); 0 content mismatches between a case and its winning source row | dbt facts on DuckDB, 2026-10-28 | [fact-summary.json](docs/evidence/task09-backfill/fact-summary.json) |
| Rebuild from ER export to facts with the backfill CLI: 7 steps, all exit 0, 99.7 s; dimensions 30/30 and facts 48/48 dbt nodes pass; 98,188 employers, 713,467 cases | 2026-10-29 | [backfill-from-er_export.json](docs/evidence/task04-er-threshold-0875/backfill-from-er_export.json), [downstream-counts.json](docs/evidence/task04-er-threshold-0875/downstream-counts.json), [dbt logs](docs/evidence/task04-er-threshold-0875/backfill-logs/) |
| Entity resolution: 161,596 employer source records (from 808,282 LCA and released USCIS rows) → 98,188 employers | Splink 5, DuckDB, 1 thread, 4 GB | [input.json](docs/evidence/task04-er-threshold-0875/input.json), [prediction.json](docs/evidence/task04-er-threshold-0875/prediction.json) |
| ER accuracy on human labels, holdout of 105 pairs (55 same / 50 different) at match threshold 0.875: TP 50, FP 0, FN 5, TN 50 → precision 1.000, recall 0.909. Same at 0.80, 0.85 and 0.90. Hard-case subset (42 pairs): TP 30, FP 0, FN 3, TN 9 | 559 pairs, one reviewer; see limitations | [report.json](docs/evidence/task04-human-acceptance-20261029/report.json) |
| Employer IDs survive a rebuild: re-predicting the full year while inheriting the archived registry changes 0 of 161,596 employer IDs | 2026-10-29 | [employer-id-diff.json](docs/evidence/er-registry-archive-20261029/employer-id-diff.json) |
| Cube REST contract on the full-year Parquet export passes (9/9 as reported by node:test): all seven metrics equal an independent SQL oracle; 713,467 rows over 280,799 five-dimension groups | local Parquet, not Snowflake | [semantic-actual.log](docs/evidence/task04-er-threshold-0875/semantic-actual.log) |
| Map projection: 713,467 cases → 517,991 frequency rows (103,036,467 bytes); 336 cases unmapped; points are Census ZCTA (686,006), place (25,396) or county (1,729) internal points | 2026-10-28 | [manifest.json](docs/evidence/task14-fullyear-map/manifest.json) |
| Map frame cost: p95 of per-frame script + render time during a scripted pan/zoom over 704 H3 cells, 240 frames: 4.9 ms at 1x CPU, 9.4 ms at 4x CPU throttling (test limits 16.7 / 33 ms) | one run; Chromium via Playwright on an RTX 4090; machine load ~7. Measures work per frame, not display rate (rAF interval p95 was 33.4 ms) | [1x](docs/evidence/task14-fullyear-map/viz-frame-fy2025-full-1x.json), [4x](docs/evidence/task14-fullyear-map/viz-frame-fy2025-full-4x.json), [log](docs/evidence/task14-fullyear-map/frame-measure.log) |
| Browser E2E on the actual data: map suite 7/7; full site run 14/14, which repeats the map suite and adds keyboard-only search → detail → compare, axe checks, both locales and themes | local build, 2026-10-28 (before the 2026-10-29 ER rerun) | [viz-e2e.log](docs/evidence/task14-fullyear-map/viz-e2e.log), [site-e2e-actual.log](docs/evidence/task14-fullyear-map/site-e2e-actual.log) |
| Column lineage from the local OpenMetadata API: 25 tables, 24 table edges, 449 column edges; 5/5 checked column chains complete | local DuckDB catalogs | [summary.json](docs/evidence/task08-lineage-fy2025/summary.json), [verify-lineage.log](docs/evidence/task08-lineage-fy2025/verify-lineage.log), [lineage.svg](docs/evidence/task08-lineage-fy2025/lineage.svg) |
| CDC drill: Postgres 16 logical replication (pgoutput) → DuckDB RAW; inserts, updates and deletes on two tables all landed within 29.44 s at 30 s polling | Docker, synthetic rows, own pgoutput reader (not Airbyte) | [drill.json](docs/evidence/task02-cdc/drill.json) |
| Backfill DAG (`airflow dags test`, FY2025): load → staging → ER → dimensions ran as Airflow tasks; the run stopped at the disk-share guard before facts (20.7 GB projected, 18 GB share at the time); the fact tasks ran afterwards on their own | 2026-10-28; staging still reported the USCIS FY2023 gate failure fixed on 2026-10-29 | [summary.json](docs/evidence/task09-backfill/summary.json), [DAG log](docs/evidence/task09-backfill/airflow-dags-test-run3.log) |
| Backfill DAG, FY2025 verification run: all 10 steps (RAW load → staging → ER → dimensions → facts) succeeded as Airflow tasks in 219.7 s of DAG wall time (the steps sum to 215.8 s); dbt seed 15/15, staging 45/45, dimensions 30/30, facts 50/50; ER 161,596 source records → 98,188 employers, inheriting the archived registry | 2026-11-06, local DuckDB, separate `verify-20261106` databases | [task09-backfill-airflow-verify-20261106/](docs/evidence/task09-backfill-airflow-verify-20261106/) |
| Snowflake foundation: Terraform state holds 22 objects plus 69 grants and attachments, 91 instances in all. Grant tests 8/8 PASS (7 live, 1 offline helper): CUBE_SVC reads all 7 marts and is denied the 5 checked RAW, STAGING and INTERMEDIATE tables; AIRBYTE_SVC, DBT_SVC and CUBE_SVC each see only `SPONSORATLAS_XS` | trial account; Terraform applied 2026-10-30, tests 2026-10-31 | `warehouse/terraform/terraform.tfstate` (local, git-ignored), [pytest-live.log](docs/evidence/task01-snowflake-grants-20261031/pytest-live.log) |
| Snowflake RAW: 20 tables match on row count and fingerprint. The 8 official files hold 1,224,291 rows, difference 0 per file. The other 12 tables are 1 empty BLS table, 5 synthetic `RAW.JOBS` fixtures and 6 exports of local `DIMENSIONS_INPUT` and `FACTS_INPUT` | 2026-10-30; loaded by script (PUT + COPY INTO as AIRBYTE_SVC), not Airbyte | [load-summary.json](docs/evidence/task02-snowflake-raw-20261030/load-summary.json), [reconcile-snowflake.csv](docs/evidence/task02-snowflake-raw-20261030/reconcile-snowflake.csv) |
| Snowflake dbt build: seed 15/15 in 36.14 s, staging 45/45 in 39.80 s, dimensions 30/30 in 14.31 s, facts 48/48 in 31.73 s; 122.0 s in total at 2 threads. The 138 executions are 110 distinct nodes (15 seeds, 18 models, 1 snapshot, 75 data tests, 1 unit test) | 2026-10-30, one run | [run_results and logs](docs/evidence/task06-snowflake-dbt-20261030/) |
| Snowflake vs the local DuckDB build: 18 relations, all row counts equal; per-column checksums 14 SAME, 4 DIFF (`dbt_scd_id`; `employer_key`, the same value renamed; `source_relation` twice, on synthetic CDC fixtures). 10 of 11 metrics equal; `certified_rate_avg_sql` differs only by Snowflake AVG rounding (0.977779). SHA-256 over 280,799 LCA groups equal | checksums only; float and timestamp columns are skipped (`scripts/snowflake_compare.py:3-5`); 12 compared columns are all NULL locally, in 5 relations, including `fact_lca_case.employer_key` and the employer, occupation and geography keys on the 86 job rows, so a match says nothing for those columns; not independent of the local build, see limitations | [compare.json](docs/evidence/task06-snowflake-dbt-20261030/compare.json) |
| Snowflake Cube REST: PASS as CUBE_SVC / ANALYST with 7 metrics, 5 dimensions, 3 views and 2 external rollups, exact against the local oracle over 280,799 groups. The exact check first caught a page bound in the verifier: pagination stopped at offset 250,000 and the groups summed to 665,213, not 713,467 | 2026-10-31 | [run.log](docs/evidence/task07-cube-snowflake-20261031/run.log), [verify-snowflake.json](docs/evidence/task07-cube-snowflake-20261031/verify-snowflake.json) |
| Snowflake policies: 5/5 PASS (3 live, 2 static): ANALYST sees a masked email, a non-owner sees 0 application rows, an owner with PII_READER sees the plain email | 2026-10-31, probe tables only; the real marts have no policies attached ([policy references](docs/evidence/task01-snowflake-grants-20261031/pytest-live.log)) | [pytest-live.log](docs/evidence/task08-snowflake-policies-20261031/pytest-live.log) |
| Snowflake cost: one experiment on an 8-partition transient clone of `fact_lca_case`, one run per phase. The two attributed queries scanned 8 of 8 partitions with and without the clustering key. Per-phase credits are null, so no per-run credit figure exists. The resource monitor read 0.81 of 20 credits afterwards (a lagging, whole-warehouse number) | 2026-11-01, one experiment | [experiment-P9A-reconciled-8h.json](docs/evidence/task10-snowflake-cost-20261031/experiment-P9A-reconciled-8h.json), [top20-remote.json](docs/evidence/task10-snowflake-cost-20261031/top20-remote.json), [credits.jsonl](docs/evidence/task10-snowflake-cost-20261031/credits.jsonl) |
| `make test` exits 0: pytest 670 passed, 12 skipped (10 live Snowflake, 1 ER acceptance, 1 Spark Connect); orchestration tests (Airflow environment) 170; Cube fixture and source tests 11 + 26; site, UI and environment tests 34 + 2 + 1; other Node tests 6 + 11 | offline suites only, 2026-11-10, on commit 82aaae3 plus the changes committed with the log | [make-test.log](docs/evidence/test-run-20261110/make-test.log) |

## Running it

Requirements: Linux x64 (developed on Ubuntu), Python 3.12 with `uv`, and Node 24.22.1 unpacked
at `.tools/node-runtime/node-v24.22.1-linux-x64` (official linux-x64 archive). Docker is needed only
for the CDC drill and OpenMetadata, a GPU only for the frame-cost test. These are the steps used on
the development machine; setup from a fresh clone has not been re-tested.

```bash
python3.12 -m venv .venv
UV_CACHE_DIR=.tools/uv-cache uv sync --frozen --extra dev --extra snowflake --extra ingestion \
  --extra staging --extra er --extra er-spark
uv venv --no-cache --python .venv/bin/python .tools/orchestration/venv
uv pip sync --no-cache --python .tools/orchestration/venv/bin/python orchestration/requirements.lock
semantic/run.sh install
bash web/scripts/bootstrap.sh

make test     # all offline suites: pytest, Airflow DAG tests, Cube on a synthetic fixture, site/UI unit tests
make check    # ruff + TypeScript typecheck
```

Live Snowflake, ER acceptance and Spark Connect tests skip unless opted in (`RUN_SNOWFLAKE_GRANTS=1`,
`RUN_SNOWFLAKE_POLICIES=1`, `SPONSORATLAS_ER_ACCEPTANCE=1`, `SPONSORATLAS_SPARK_CONNECT_TEST=1`).

Rebuilding from the official files needs them archived in `data/raw` first (not in git; see
[ingestion/README.md](ingestion/README.md)):

```bash
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill readiness --fiscal-year 2025
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill run --fiscal-year 2025 \
  --output <summary.json> --logs <dir>   # RAW load → staging → ER → dimensions → facts
make staging-actual        # staging build with gate and reconciliation summary
make semantic-actual       # Parquet export + Cube REST contract against an SQL oracle
make viz-worksite-export   # map projection
make viz-frame-build       # production site build for the browser tests
make viz-frame-measure     # frame-cost test (takes the GPU lock)
make viz-e2e               # map browser tests; make site-e2e-actual runs the site journeys
make cdc-drill             # throw-away Postgres container → DuckDB RAW
```

## Repository layout

| Path | Contents |
|---|---|
| `ingestion/` | release registry, Airbyte Terraform (not applied), public job-board source, Postgres CDC setup |
| `scripts/` | archiving, RAW loaders, reconciliation, reference-data builders, CDC drill, data checks |
| `dbt/` | staging, intermediate and mart models, SCD2 snapshot, tests, seeds, source contracts |
| `er/` | employer entity resolution, registry, label import and evaluation, Databricks adapter |
| `orchestration/` | Airflow 3 + Cosmos DAGs, fiscal-year backfill, isolated runtime |
| `semantic/` | Cube model, REST server wrapper, Parquet and map-projection exports |
| `web/` | `apps/site` (Next.js 16 site), `packages/ui` (design tokens and components), test scripts |
| `governance/` | private applications contract, local OpenMetadata, lineage export and checks |
| `warehouse/` | Snowflake Terraform (applied to a trial account on 2026-10-30), bootstrap notes, live grant and policy tests |
| `azure/` | Azure Data Factory pipeline and Fabric SQL/notebook port with offline tests (not provisioned) |
| `bi/` | script-generated Power BI project and Tableau workbook, reconciliation SQL; `bi/public` builds the static [Tableau Public workbook](https://public.tableau.com/app/profile/simon.d5578/viz/SponsorAtlas-public/SponsorAtlaspublicsnapshot) published on 2026-11-10 and reconciled 5/5 (see limitations) |
| `data/reference/` | Census, CBSA and SOC reference files pinned by SHA-256 |
| `docs/evidence/` | raw outputs behind the results table ([index](docs/evidence/README.md)) |
| `docs/dev/` | per-task development evidence notes and the Snowflake bootstrap runbook |

## Not done and limitations

- **Snowflake is a trial-account run, not production.** The trial started 2026-10-25 and ends
  2026-11-24; both dates are derived from query history and the resource monitor, not read from a
  billing page ([run.log](docs/evidence/task07-cube-snowflake-20261031/run.log)). It has no production users. Since 2026-11-06 two Airflow DAGs
  have run against it from a development deployment on one home machine (LocalExecutor, SQLite
  metadata, systemd --user units). The 5 scheduled daily runs from 2026-11-06 to 2026-11-10 and the
  scheduled weekly run on 2026-11-08 succeeded in every task, each on its first try
  ([orchestration/README.md](orchestration/README.md)). The schedules end before the trial does: the
  last daily run is due 2026-11-22 08:00 UTC and the last weekly run 2026-11-15 09:00 UTC.
  RAW was loaded by script, not
  Airbyte. ER ran locally: Snowflake dimensions and facts read script-loaded exports of the local ER
  output and `FACTS_INPUT` ([load-summary.json](docs/evidence/task02-snowflake-raw-20261030/load-summary.json)), so Snowflake is not
  independent of the local build.
- **Other cloud and native tools were not run.** Databricks: Spark Connect adapter and job template,
  never run. Azure: ADF pipeline and Fabric SQL/notebook with offline tests, never provisioned.
  Airbyte: configuration as code only; the local `abctl` install failed on this host (kube-proxy
  "too many open files") and has not been re-run.
- **BI is partly met.** The files in `bi/powerbi` and `bi/tableau` are generated by script and were
  never opened in either tool. The five-number reconciliation matches Cube against SQL but fails for
  both dashboards, which have no values, on an older 238,891-case snapshot
  ([reconciliation.json](bi/evidence/reconciliation.json)). `bi/public` is the public dashboard path: its generated
  workbook, built from a Snowflake snapshot taken on 2026-11-06, opened in Tableau Public 2026.2.3 with no
  error and the queries behind all four sheets returned rows
  ([evidence](docs/evidence/task11-tableau-public-20261105/desktop-load-20261106.json)). It is a static snapshot, not connected to Snowflake.
  That check read the app's log, not its screen. The workbook was published on Tableau Public on
  2026-11-10 at 19:37 UTC ([SponsorAtlas public snapshot](https://public.tableau.com/app/profile/simon.d5578/viz/SponsorAtlas-public/SponsorAtlaspublicsnapshot)).
  It holds snapshot `20261106T052440Z-aa007296`, exported from the Snowflake public marts on
  2026-11-06 at 05:24:40 UTC, before any scheduled Airflow run, so it has no data from those runs (its
  86 posting rows are the 2026-10-24 archive rows), and it does not refresh. On 2026-11-10 at
  19:39 UTC `reconcile_public.py` read the published Reconciliation sheet: 5 of 5 metrics PASS against
  the snapshot at tolerance 1e-8 (the sheet's CSV export gives `certified_rate` with 9 decimals,
  within the tolerance; [evidence](docs/evidence/task11-tableau-public-20261105/published-20261110/)).
  The other three published sheets have 0 differences
  from DuckDB over the snapshot CSVs, apart from display rounding. Employers are shown by ID, not
  name, but `case_id` is the raw DOL case number, and workbook download is on, so anyone can download
  the full 713,467-row case table.
- **No site deployment.** The repository has been public at github.com/simon-jd/SponsorAtlas since
  2026-11-03 (GitHub repository metadata), but there is no public site and no real-user monitoring,
  and [web.yml](.github/workflows/web.yml) has no recorded run on GitHub. The site runs only locally;
  quality gates (Lighthouse, bundle budget, visual diffs) ran locally on fixture data. No native
  Figma file exists. The only running service deployment is the development Airflow one on the build
  machine.
- **Governance is partial.** Lineage is local. Snowflake row-access and masking policies passed live
  on probe tables only ([log](docs/evidence/task08-snowflake-policies-20261031/pytest-live.log)); the real marts have no policies attached
  ([log](docs/evidence/task01-snowflake-grants-20261031/pytest-live.log)). Failure alerts are not delivered: they were drilled against a local
  HTTP sink on 2026-10-24, and the Snowflake DAGs append them to a local log
  (`.tools/orchestration/alerts/alerts.jsonl`) that notifies nobody. Its 6 entries are all from the
  two failed manual runs on 2026-11-06. A deadline alert fires 30 minutes after a run was queued
  unless the run has succeeded by then; the two so far fired after their manual runs had failed.
- **Cost evidence is thin.** One experiment on an 8-partition clone shows no partition pruning, and
  clustering completion is unknown. Per-phase credits are null, so there is no per-run credit figure
  ([experiment](docs/evidence/task10-snowflake-cost-20261031/experiment-P9A-reconciled-8h.json)).
- **ER evaluation caveats.** The 0.875 threshold was chosen after the results on a holdout labelled
  by a sub-agent had been seen, so the holdout numbers are not an unbiased estimate. The human labels
  come from one reviewer with no second review, and the workbook's evidence column was left empty.
  The holdout is small: 105 pairs, recall 50/55, so one missed pair moves recall by about 1.8 points.
  The deployed clusters come from the unsupervised model; the model retrained with the labels gives
  identical clusters (0 of 161,596 records differ).
- **Coverage.** One full fiscal year (FY2025) plus FY2024 Q4 for LCA; USCIS FY2022–FY2023 only. The
  local job-posting fact and the published Tableau snapshot have 86 real vendor-board rows
  (Greenhouse 19, Ashby 67) from one snapshot on 2026-10-24, replay kind `LOCAL_ARCHIVE_REPLAY`
  (`FACTS_INPUT.job_observations`, `scripts/load_fact_inputs.py:176`, [load-summary.json](docs/evidence/task02-snowflake-raw-20261030/load-summary.json)). On
  Snowflake the daily DAG also loads one UTC day of public board observations per run into
  `RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS` (13 boards: Greenhouse 7, Lever 3, Ashby 3; 4,904 to 4,911
  rows a day from 2026-11-06 to 2026-11-10). The runs from 2026-11-07 fetched the boards live at about
  08:00 UTC. The 2026-11-06 run reused the archive a manual run had fetched at 05:26 UTC, although its
  replay kind is also recorded as `LIVE_BOARD_FETCH`. Seven boards returned byte-identical responses
  on some consecutive days, so many rows are re-observations. Postings are not linked to LCA
  employers (`employer_mapping_status` is `UNRESOLVED_SOURCE_EMPLOYER`) and say nothing about the
  hiring market. `RAW.JOBS`, `stg_board_jobs` and `stg_cdc_*` hold synthetic ACME and CDC
  fixtures that feed only tests.
- **Map.** Points are Census representative points of ZIP areas or places, not addresses. H3
  aggregation runs in the Next.js server (h3-js), not in DuckDB or Snowflake. Frame cost was measured
  once, on an RTX 4090, not on low-end hardware.
- **Site.** It shows employer identifiers, not company names (names are not in the public
  projection). Some page text still describes the coverage as "Q4 releases", although the data now
  covers FY2024 Q4 and all of FY2025.
- **Timings** are single runs on a shared machine (load averages between about 2 and 11).

The data is public. Nothing here is legal advice.
