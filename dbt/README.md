# dbt project

dbt Core 1.12.6 models for the warehouse. Locally they run on DuckDB (`dbt-duckdb`); the
Snowflake target (`dbt-snowflake`, key-pair auth as DBT_SVC) ran green on a trial account on 2026-10-30,
and from Airflow since 2026-11-06: the full public build on 2026-11-06 and 2026-11-08, and the
job-posting fact every day.

| Path | Contents |
|---|---|
| `models/staging/` | `stg_lca`, `stg_uscis`, `stg_bls`, job-posting and CDC staging; explicit per-release column maps, SOC/date/state/county normalisation, annual wage (hour × 2080, week × 52, bi-weekly × 26, month × 12, year × 1; unknown units give null) |
| `models/intermediate/` | candidate cases (latest status per case number), dimension mapping, current employer attributes, BLS benchmark candidates |
| `models/marts/` | `dim_employer`, `dim_occupation`, `dim_geography`, `dim_date`; `fact_lca_case`, `fact_wage_benchmark`, `fact_job_posting_daily` with enforced contracts |
| `snapshots/snap_employer.sql` | SCD2 employer history from the ER output |
| `tests/` | singular gates: unmapped source columns, flag limits per fiscal year, SCD2 overlap, fact grain and accounting, provenance; `tests/unit/` wage annualisation |
| `seeds/` | column maps, flag limits, state/county/CBSA/ZCTA/place, SOC 2018 and BLS OEWS 2025 reference seeds |
| `source_contracts/` | pinned headers and SHA-256 of the verified official releases |

`dbt/run.sh` wraps the project's dbt and keeps logs, targets and the DuckDB file under
`.tools/` (`SPONSORATLAS_DUCKDB_PATH`, `DBT_TARGET_PATH`, `DBT_LOG_PATH` override them).

Synthetic data (no source files needed):

```bash
.venv/bin/python -m scripts.staging_fixture       # new labelled synthetic RAW database
dbt/run.sh seed
dbt/run.sh build --select staging
.venv/bin/python -m scripts.staging_checks        # mutation checks on a separate database
```

Real data (archived official files in `data/raw`):

```bash
make staging-actual   # staging build on .tools/staging/fy2025-full.duckdb plus a gate/reconciliation summary
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill run --fiscal-year 2025 \
  --output <summary.json> --logs <dir>     # full chain: RAW load -> staging -> ER -> dimensions -> facts
```

The backfill writes the canonical databases under `.tools/` and refuses to overwrite existing
ones; `--from-step <step>` reruns one step and everything after it.

Snowflake (`SNOWFLAKE_ACCOUNT` and `SNOWFLAKE_PRIVATE_KEY_FILE` for DBT_SVC in the environment,
RAW loaded by `scripts.load_snowflake_raw`). The same four stages as the backfill; Terraform has
no `SNAPSHOTS` schema, so the snapshot goes to INTERMEDIATE:

```bash
export DBT_TARGET=snowflake   # plus the two variables above
dbt/run.sh seed
dbt/run.sh build --select staging
dbt/run.sh --warn-error build --indirect-selection cautious --vars '{snapshot_schema: INTERMEDIATE}' --select <DIMENSION_SELECT>
dbt/run.sh --warn-error build --indirect-selection cautious --vars '{snapshot_schema: INTERMEDIATE}' --select <FACT_SELECT>
.venv/bin/python -m scripts.snowflake_compare --output compare.json   # vs the local DuckDB build
```

`DIMENSION_SELECT`/`FACT_SELECT` are in `orchestration/sponsoratlas_orchestration/backfill.py`.
Result 2026-10-30: seed 15/15 in 36.14 s, staging 45/45 in 39.80 s, dimensions 30/30 in 14.31 s,
facts 48/48 in 31.73 s; 122.0 s at 2 threads. The 138 executions are 110 distinct nodes (15 seeds,
18 models, 1 snapshot, 75 data tests, 1 unit test)
([run results and logs](../docs/evidence/task06-snowflake-dbt-20261030/)).

Comparison with the local DuckDB build
([compare.json](../docs/evidence/task06-snowflake-dbt-20261030/compare.json)): 18 relations, all row
counts equal; per-column checksums 14 SAME, 4 DIFF (`dbt_scd_id`; `employer_key`, the same value
renamed; `source_relation` twice). 10 of 11 metrics equal (`certified_rate_avg_sql` differs only by
Snowflake AVG rounding); SHA-256 over 280,799 LCA groups equal. The checksums skip float and timestamp
columns (`scripts/snowflake_compare.py:3-5`). Snowflake dimensions and facts read script-loaded
exports of the local ER output and `FACTS_INPUT`
([load summary](../docs/evidence/task02-snowflake-raw-20261030/load-summary.json)), so this build is
not independent of the local one.

Job data: `fact_job_posting_daily` reads `facts_input.job_observations`
(`models/intermediate/fact_sources.yml:11-12`, on Snowflake `RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS`).
Locally that is 86 real vendor-board rows from one snapshot on 2026-10-24, replay kind `LOCAL_ARCHIVE_REPLAY`
(`scripts/load_fact_inputs.py:176`). On Snowflake, `scripts/load_board_observations.py` also
replaces one UTC day of public board observations per daily DAG run since 2026-11-06 (13 boards;
4,904 to 4,911 rows a day from 2026-11-06 to 2026-11-10). Its replay kind is always recorded as
`LIVE_BOARD_FETCH`, although the 2026-11-06 run reused an archive a manual run had fetched at
05:26 UTC. `fact_job_posting_daily` is built incrementally per observation date.
`stg_board_jobs` and `stg_cdc_*` read the synthetic ACME and CDC
fixtures in `RAW.JOBS` and feed only tests (`tests/board_provenance.sql`, `tests/cdc_quality.sql`).

Last full-year results: staging 38 tests pass and 7 models build
([summary](../docs/evidence/task03-uscis-release-20261029/summary.json)); dimensions 30/30 and
facts 48/48 ([logs](../docs/evidence/task04-er-threshold-0875/backfill-logs/)).
