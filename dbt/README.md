# dbt project

dbt Core 1.12.6 models for the warehouse. Locally they run on DuckDB (`dbt-duckdb`); the
Snowflake target (`dbt-snowflake`, key-pair auth as DBT_SVC) ran green on a trial account on 2026-10-30.

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
Result 2026-10-30: 15 + 45 + 30 + 48 PASS, marts identical to DuckDB
([evidence](../docs/evidence/task06-snowflake-dbt-20261030/)).

Last full-year results: staging 38 tests pass and 7 models build
([summary](../docs/evidence/task03-uscis-release-20261029/summary.json)); dimensions 30/30 and
facts 48/48 ([logs](../docs/evidence/task04-er-threshold-0875/backfill-logs/)).
