# Ingestion

How official files and job data reach RAW. Locally, RAW is a DuckDB database. On the Snowflake trial
account RAW was loaded by script on 2026-10-30 (`scripts.load_snowflake_raw`: PUT to a table stage and
COPY INTO, as AIRBYTE_SVC / LOADER), not by Airbyte: 20 tables match, among them the 8 official files
with 1,224,291 rows and difference 0
([load summary](../docs/evidence/task02-snowflake-raw-20261030/load-summary.json),
[reconciliation](../docs/evidence/task02-snowflake-raw-20261030/reconcile-snowflake.csv)). Since
2026-11-06 the daily Airflow DAG also loads job-board observations into RAW with
`scripts.load_board_observations` (a script as AIRBYTE_SVC / LOADER, not Airbyte). The Airbyte
to Snowflake path exists only as Terraform, not applied, and no Airbyte credentials exist.

| Path | Contents |
|---|---|
| `releases.json` | registry of official releases (DOL LCA FY2024 Q4 and FY2025 Q1–Q4, USCIS H-1B Employer Data Hub FY2022–FY2025, BLS OEWS May 2024/2025) with URLs, reader settings and, where verified, pinned SHA-256 |
| `main.tf`, `sources.tf`, `connections.tf` | Airbyte sources, destinations and manual-trigger connections (not applied) |
| `board_api/manifest.yaml` | declarative source for public Greenhouse, Lever and Ashby job boards (GET only) |
| `postgres/` | logical-replication settings, CDC setup SQL (pgoutput slot, publication, replica identity) and a demo schema |
| `deploy/` | pinned `abctl` installer and an Airbyte deploy script for this host |
| `tests/mock.tftest.hcl` | Terraform test with mocked providers |

## Archive official files

```bash
.venv/bin/python -m scripts.archive_sources ingestion/releases.json --release lca_fy2025_q1
```

This downloads one release from its official URL, refuses a SHA-256 that differs from a
pinned one, and writes `data/raw/<source>/<release>/<RELEASE>__<sha256>.<ext>` plus a JSONL
copy and a `.provenance.json` (URLs, HTTP metadata, hashes, row count, original headers,
sheet or ZIP member). An existing hash path is never overwritten. `data/raw` is not in git.
USCIS FY2024/FY2025 have no official URL configured yet, and BLS 2024 has no verified file
(its download returned HTTP 403).

Each RAW row keeps `SOURCE_RELEASE`, `SOURCE_SHA256`, `SOURCE_URL`, `SOURCE_ROW_NUMBER` and
`PAYLOAD_JSON` (all original headers as strings); staging maps the payload per release and
fails on unmapped columns.

Row counts per file are compared with RAW by `scripts.reconcile_counts` (Snowflake, or a local
DuckDB copy with `--duckdb`; exit 1 on any mismatch).

Job data on Snowflake: `RAW.JOBS` holds only synthetic ACME and CDC fixtures that feed only tests. The
script load above put 86 real vendor-board rows (Greenhouse 19, Ashby 67) from one snapshot on
2026-10-24, replay kind `LOCAL_ARCHIVE_REPLAY`, into `RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS`, the table
behind `fact_job_posting_daily`
(`scripts/load_fact_inputs.py:176`, [load summary](../docs/evidence/task02-snowflake-raw-20261030/load-summary.json)).
Since 2026-11-06 each daily DAG run replaces only that UTC day's rows with public board observations
from the 13 boards in `board_api/live_boards.yaml` (4,904 to 4,911 rows a day from 2026-11-06 to
2026-11-10); the loader does not touch other dates, including the 86 archived rows. The runs from
2026-11-07 fetched the boards live. The 2026-11-06 run reused an archive a manual run had fetched at
05:26 UTC; the replay kind is recorded as `LIVE_BOARD_FETCH` for every run, fetched or reused.
These rows are not linked to LCA employers.

## Change data capture

`make cdc-drill` starts a throw-away Postgres 16 container (`wal_level=logical`), loads the demo
schema, applies `postgres/configure_cdc.sql`, and runs `scripts/cdc_drill.py`: a pgoutput reader
that copies inserts, updates and deletes on `job_postings` and `application_events` into a
DuckDB RAW file. The container is removed on exit. Last run: all six changes landed within
29.44 s at 30 s polling ([drill.json](../docs/evidence/task02-cdc/drill.json)). This is a
synthetic local drill, not Airbyte CDC.

## Not done

- Airbyte: `make airbyte-up` (abctl 0.30.4, kind) failed on this host because kube-proxy hit
  "too many open files"; it has not been re-run since the host limits were raised.
- No Airbyte sync to Snowflake has run, and `scripts.cdc_probe` (Airbyte CDC acceptance) has not been
  executed. The live reconciliation has run, on the script-loaded RAW only
  ([reconciliation](../docs/evidence/task02-snowflake-raw-20261030/reconcile-snowflake.csv)).

Offline checks: `make test` covers the archive, reconciliation, board manifest and CDC code;
`terraform validate` / `terraform test` need `terraform init`, which downloads providers, so they
are not part of `make test`.
