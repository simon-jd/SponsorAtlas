# SponsorAtlas orchestration

Two scheduled DAGs use Airflow **3.3.2**, Cosmos **1.15.1**, and the existing native dbt **1.12.6** environment. Both have run on the Snowflake trial since 2026-11-06 from the development deployment described under Scheduled deployment:

- `sponsoratlas_daily`, 08:00 UTC from Nov 6 to Nov 22, 2026: `preflight` → `load_boards` → `observations_freshness` → `dbt_job_fact` → `job_gates` → `record_usage`.
- `sponsoratlas`, Sundays 09:00 UTC on Nov 8 and Nov 15, 2026: `preflight` → `observations_freshness` → full public dbt build → `public_quality_gates` → `cube_refresh` → `record_usage`.

`load_boards` runs `scripts.load_board_observations` for the run's UTC date without `--refetch`, so a complete archive for that date in `.tools/boards/<date>/` is reused instead of fetched again; only `archive.reused` in the board manifest under `.tools/orchestration/artifacts/` shows which happened. `replay_kind` is the constant `LIVE_BOARD_FETCH` for every row the loader writes, fetched or reused. The load replaces only that date's rows in `RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS`. `fact_job_posting_daily` is delete+insert by observation date and re-selects every date from its latest observation date minus one day, so a daily run rewrites up to three observation dates, not only the new one. In the full public build, `fact_lca_case` and `fact_wage_benchmark` are recomputed in full from rebuilt intermediates and applied with a MERGE over all rows (713,467 and 237,514 rows on 2026-11-08); the dimensions, staging and intermediate tables are recreated.

`requirements.lock` pins the isolated Airflow dependency set, resolved against the official Airflow Python 3.12 constraints. No root dependency lock is changed.

```bash
uv venv --no-cache --python .venv/bin/python .tools/orchestration/venv
uv pip sync --no-cache --python .tools/orchestration/venv/bin/python orchestration/requirements.lock
orchestration/run.sh airflow db migrate
orchestration/run.sh pytest -q orchestration/tests
.tools/node-runtime/node-v24.22.1-linux-x64/bin/node --max-old-space-size=768 --test --test-concurrency=1 --test-force-exit orchestration/tests/cube_revision.test.cjs orchestration/tests/cube_external_refresh.test.cjs
```

`orchestration/run.sh` scopes Airflow state, metadata, logs, DAGs and dbt artifacts to this project. It uses `LocalExecutor` (parallelism two; each DAG permits one task and one run at a time), disables examples, telemetry and Cosmos metastore/partial-parse caching. Rendering uses credential-free native `dbt ls`; execution uses the orchestration wrapper around the existing native dbt CLI. Each task has two retries and a 15-minute execution timeout; `preflight` waits five minutes between attempts. `dagrun_timeout` fails a run after 30 minutes (`sponsoratlas_daily`) or 45 minutes (`sponsoratlas`) and stops its tasks. In `sponsoratlas`, each model/seed/snapshot runs `dbt build --indirect-selection cautious`; the final test task explicitly selects all public tags and the fifteen reviewed singular gates. This final selection includes all thirteen fact foreign keys, enforced model contracts, grain, accounting, dimension and legacy staging gates. Private application models remain disabled.

## Production opt-in

Fill the key file paths, trial timestamps and alert transport in `config.production.env.example` using secure runtime configuration; the systemd deployment below uses `systemd/airflow.env.template` instead. An unfilled configuration fails at runtime. No DAG has run against real Airbyte, Slack or SMTP, and no Airbyte, Slack or SMTP credentials exist. Both scheduled DAGs have run against the Snowflake trial since 2026-11-06 (see Scheduled deployment). The Snowflake runs of 2026-10-30 to 2026-11-01 used dbt and scripts directly, not Airflow ([dbt](../docs/evidence/task06-snowflake-dbt-20261030/), [Cube](../docs/evidence/task07-cube-snowflake-20261031/run.log), [cost](../docs/evidence/task10-snowflake-cost-20261031/run.log)). The scheduled DAGs no longer call Airbyte: job observations come from `scripts/load_board_observations.py`. The reviewed `DIMENSIONS_INPUT` / `FACTS_INPUT` mapping contracts must already exist; this task does not invent upstream ER or full-year mappings. On the trial account `RAW.LCA.DIMENSIONS_INPUT_*` and `RAW.LCA.FACTS_INPUT_*` exist as script-loaded exports of the local build ([load summary](../docs/evidence/task02-snowflake-raw-20261030/load-summary.json)); since 2026-11-06 `RAW.LCA.FACTS_INPUT_JOB_OBSERVATIONS` also gets one UTC day of public board rows per daily DAG run from `scripts/load_board_observations.py`; no Airbyte connection exists.

SQLite metadata with LocalExecutor, used by the rehearsal and the systemd units, is a development deployment; a production one needs a production metadata database. Keep service identities separate: native dbt uses DBT_SVC/TRANSFORMER and `SNOWFLAKE_PRIVATE_KEY_FILE`; Cube uses CUBE_SVC/ANALYST and `SPONSORATLAS_CUBE_PRIVATE_KEY_FILE`; the board loader uses AIRBYTE_SVC/LOADER and `SNOWFLAKE_LOADER_PRIVATE_KEY_FILE`; preflight's metered total uses COST_OBSERVER_SVC/COST_OBSERVER and `SNOWFLAKE_COST_OBSERVER_PRIVATE_KEY_FILE`. All use JWT/key pairs and SPONSORATLAS_XS. Cube's existing preflight retains XS/auto_suspend=60 and the public-mart allowlist.

The wrapper rejects missing, inactive or >30-day trial windows before cloud dispatch. Admission requires enough time for native dbt's bounded 840-second process plus cancellation/cleanup before the seven-minute trial safety margin. It recomputes `min(840, cutoff-now-60)` immediately before spawning, detects late returns, and adds session statement240s/queue60s/abort-detached controls to native Snowflake connections. It never changes the global profile.

## Airbyte state (retained, not scheduled)

`runtime.airbyte_sync` stays with its recovery tests, and no scheduled DAG calls it. It needs a durable shared `SPONSORATLAS_AIRBYTE_STATE_DIR` accessible to all workers. Airbyte connections must be **Manual**, so Airflow is their only scheduler. Airbyte polling leaves 60 seconds of its 900-second total budget for DELETE cancellation and confirmation; unresolved cancellation is persisted, alerted and blocks retry until reconciled.

Every `(endpoint, connection)` has an exclusive advisory lock and durable active authority, with a separate audit record for every run. Workers must share a filesystem supporting POSIX flock, atomic rename and directory fsync. State files are created0600; initial directories and every replacement are fsynced. Both active authority and per-run audit commit **before** the first token request/POST. Never unlink the persistent `.lock` inode. Unresolved `trigger_pending`, submitted or `cancel_unconfirmed` blocks **all later run IDs**, even after the Airflow run finishes. When the connection index is absent, matching old per-run unresolved records are adopted conservatively; multiple unresolved legacy records require reconciliation.

New work alone requires960s trial admission. A known job can be recovered near cutoff solely within its original900s total and absolute trial cutoff; close windows enter same-job cancellation. Real urllib timeouts remain typed and redacted. Any known-job transport failure enters at most60s cleanup; terminal responses after cutoff cannot confirm success. Unconfirmed cancellation is durable and blocks new POST. Confirmed terminal results are committed and cached; a failed/cancelled run never creates a replacement job. A later run may acquire triggering authority only after confirmed termination.

For a lost POST response or unresolved job, first inspect the exact Airbyte connection/job history. Reconciliation must hold the same connection flock and durably update **both active authority and its per-run audit** with the verified job ID/terminal result using the state commit interface. Unknown outcomes remain blocked. Never delete pending state/index or manually mark a guessed terminal result to force retry.

## Controlled Cube maintenance

`semantic/scripts/refresh.cjs` holds an exclusive per-work lease shared with `semantic/server.cjs`. Stop the gateway before maintenance. A live gateway or second refresh is refused. `runtime.cube_refresh` reads `work-owner.json` before it starts Node: it removes and logs a lock that names a refresh whose recorded pid no longer exists, which is what a SIGKILL or the 300-second timeout leaves behind, and fails with a message naming any other owner. A live or reused pid, a gateway or an unreadable lock needs reconciling by hand. No kill-by-name, public admin route, or expanded Snowflake write permissions is used.

The worker creates a validated random revision, invokes a private loopback Cube and loads only the two counts rollups with top-level REST `cache: "must-revalidate"`. It shares one source driver between preflight and execution, closes every driver, independently reopens the physical file READ_ONLY and verifies both new rollups and counts, then atomically promotes the owner-only ready state. A failed build retains the preceding ready revision. Restart the gateway with the **same SEMANTIC_WORK** to reuse those physical tables. The maintenance worker must be the sole owner of that work directory; external DuckDB storage is never written concurrently with a serving process. Directory permissions are tightened to 0700 and revision/ownership files are 0600.

For the explicit local synthetic mode, the Cube task exports a **new** immutable public projection from the separate orchestration dbt database, checks dimension/fact uniqueness and projection row counts, and reads its SHA256 manifest. It never modifies the protected Task6 database, original observation dates, or public projection definitions. In production mode (`SPONSORATLAS_ORCHESTRATION_MODE=production`, the mode the scheduled DAGs use on this development deployment), the refresh instead reads the public Snowflake marts directly through the existing read-only driver.

## Local rehearsal and stopping

`python -m orchestration.sponsoratlas_orchestration.demo prepare` must use the root `.venv` and creates a new labelled synthetic DB; it refuses overwrite. `demo serve --port 18789` is a synthetic HTTP transport, not Airbyte or Slack. Configure explicit `synthetic_http`, local target, parquet source, local API credentials and local_http alert URL before running a rehearsal. These fixtures are never a production fallback.

After migration, `python -m orchestration.sponsoratlas_orchestration.supervise` in the isolated environment starts a loopback execution API (18780), DAG processor and scheduler with network log serving disabled. It owns the complete process trees, samples aggregate RSS, stops above 8GB/1200s, and reaps its processes on SIGINT/SIGTERM or when `.tools/orchestration/STOP` appears. Remove that marker explicitly before a new run. Stop the separate synthetic HTTP sink too. `demo prepare` keeps the archived observation dates, so `observations_freshness` is expected to fail a rehearsal (not run since the check moved to `observed_at_utc`). A rehearsal on this metadata database after 2026-11-06 would also add a ledger row; `bin/export_runs.py` does not count it as evidence because its manifest shows synthetic mode. `SPONSORATLAS_ENABLE_DEADLINE_PROBE=1` enables a separate scheduler acceptance DAG with a five-second deadline and 25-second task; it is absent by default.

Each scheduled DAG's 30-minute `DeadlineAlert` uses **DAGRUN_QUEUED_AT**, so historical logical dates do not immediately trigger runtime alerts. This is an experimental Airflow 3 API. The scheduler fires every deadline that has passed, whatever the run's state (`airflow/jobs/scheduler_job_runner.py`), and Airflow 3.3.2 deletes a run's deadline only when the run succeeds (`airflow/models/dagrun.py`). So the alert fires 30 minutes after a run was queued unless the run has succeeded by then: after the end of a run that failed sooner, or during a run that is still going. A weekly run (45-minute timeout) still going at 30 minutes alerts and may then succeed; on 2026-10-24 the deadline probe alerted during a run that then succeeded. Both deadline alerts so far, at 05:56 and 06:01 UTC on 2026-11-06, fired 29 and 22 minutes after their manual runs had failed. Source freshness is separate: the maximum `observed_at_utc` of `source:facts_input.job_observations` must be within 24 hours. `freshness()` fails unless dbt reports that source as `pass`, so dbt's "Nothing to do" never passes.

## Scheduled deployment

`systemd/` holds three user services (execution API on 127.0.0.1:18780, DAG processor, scheduler), which do not start while `.tools/orchestration/STOP` exists, and an end timer at 2026-11-23 00:30 UTC. The units have run since 2026-11-06 (first started 05:25 UTC), with both DAGs unpaused and STOP absent. The host was down from about 04:54 to 07:22 UTC on 2026-11-08; the units restarted at 07:22 UTC and no scheduled slot fell in that window. Scheduled daily runs from 2026-11-06 to 2026-11-10 each finished 6/6 tasks on the first try, in 58 to 91 s. The 2026-11-06 daily run did not fetch: it reused the archive a manual run had fetched at 05:26 UTC (4,905 rows). The 2026-11-07 to 2026-11-10 runs fetched from the 13 boards in `ingestion/board_api/live_boards.yaml` (Greenhouse 7, Lever 3, Ashby 3) at about 08:00 UTC, 4,904 to 4,911 rows a day. Seven boards returned byte-identical bodies on some consecutive days, so many rows are re-observations of the same postings. The scheduled weekly run on 2026-11-08 finished 39/39 tasks on the first try in 485 s, with 180 dbt node results and 0 failures; its `cube_refresh` read Snowflake. The last daily run is due 2026-11-22 08:00 UTC and the last weekly run 2026-11-15 09:00 UTC. Run manifests are in `docs/evidence/task09-airflow-snowflake-20261106/runs/`. [`dag-runs.json`](../docs/evidence/task09-airflow-snowflake-20261106/dag-runs.json), exported on 2026-11-10 at 20:25 UTC, lists 16 runs of the two DAGs; 8 count as evidence: the 6 scheduled runs, `manual_first_snowflake_20261106_r2` and `manual_full_snowflake_20261106_r2`.

```bash
orchestration/systemd/install.sh            # units, ~/.config/sponsoratlas/airflow.env (0600), airflow db migrate
orchestration/systemd/install.sh --enable   # enable the end timer and services; STOP still keeps the services down
orchestration/systemd/install.sh --unpause sponsoratlas_daily --unpause sponsoratlas
orchestration/systemd/rollback.sh           # disable units, restore STOP, print the Snowflake cleanup without running it
orchestration/bin/status.sh                 # runs, ledger, metered total, STOP, alerts, next runs; reads state only
```

The repository holds no Snowflake account identifier: `install.sh` takes it from `SNOWFLAKE_ACCOUNT` in its environment when it creates the env file.

`guard.preflight` skips a run while STOP exists or with under one hour of trial left. It writes STOP at 2.5 estimated ledger credits, when the metered account total plus this run's cap reaches 123 credits, or when metering fails on two UTC dates with no good read between them. Two DAGs failing on one date are one outage. A zero or undated total counts as a metering failure, and so does a latest usage date over two days old once a run admitted at least two days ago should show in the view. The first good read stays in `guard-state.json` and each run manifest as `metered_baseline`, so the absolute limit can be audited.

STOP is rechecked by `load_boards`, `cube_refresh` and the dbt wrapper, so a marker written mid-run refuses the run's remaining paid work; a statement already running finishes or hits its own timeout. On Snowflake the only hard stop is the resource monitor's 20-credit quota on the warehouse.

Ledger credits are estimates from wall time, not metering. `record_usage` charges (wall seconds + 60) / 3600, capped at the run's timeout plus that minute: 0.5167 credits daily, 0.7667 weekly. The DAG failure callback, which also fires on `dagrun_timeout`, charges a failed run the same way and never under 0.25. A run nobody settled stays at 0.25. Clearing failed tasks keeps one ledger row, and Airflow gives the cleared run a new timeout window, so a repaired run is charged at most one cap although both attempts spent; the metered total is what catches that.

`record_usage` writes one manifest per run to `docs/evidence/task09-airflow-snowflake-20261106/runs/`, with the Snowflake account recorded as `redacted`. The 8 manifests written before 2026-11-10 were redacted in place; nothing else in them changed, and the originals stay on the deployment host. `bin/export_runs.py`, which the end timer runs, writes `dag-runs.json` next to that folder. A run counts as evidence only when every task instance succeeded, it started on or after 2026-11-06, and its manifest shows `orchestration_mode` `production`, the snowflake dbt target, a Snowflake identity on SPONSORATLAS_XS (from preflight's metering connection) and, for the daily DAG, a board load in snowflake mode. The run manifest does not copy `archive.reused`, so the rule cannot tell a reused archive from a live fetch: the 2026-11-06 scheduled daily run, which reused an archive, counts as evidence. Alerts are appended to `.tools/orchestration/alerts/alerts.jsonl` and notify nobody. All 6 entries in that file are from the two manual runs that failed on 2026-11-06.

## Fiscal-year backfill

Employer IDs survive a rebuild: the `er_predict` step runs `er.registry_archive`, which inherits the latest registry in `.tools/er/registry-archive-fy<year>` and archives the new one there (files named by SHA-256, `LATEST` names the newest). A merge, split or retraction stops the step with `IDENTITY_REVIEW_REQUIRED` (exit 3) for review. `backfill run --initialize-registry` starts new employer IDs instead (decided 2026-10-29).

```bash
# Read-only: are all four quarters archived, SHA-matched and registered as dbt sources?
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill readiness --fiscal-year 2025
# Rebuild RAW load -> staging -> ER -> dimensions -> facts into the canonical local databases
# (the loaders refuse an existing destination, dbt steps rebuild in place; --from-step <step> reruns one step and everything after it).
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill run --fiscal-year 2025 \
  --output <summary.json> --logs <dir>
```

The manual `sponsoratlas_backfill` DAG runs the same steps as Airflow tasks. Its conf must name what
it writes: a label (below), or `{"fiscal_year": 2025, "overwrite_canonical": true}`, which rewrites
the canonical databases in place and writes each step's timing JSON and log to
`docs/evidence/task09-backfill/`. Any other conf, including none or a mistyped label key, fails at
the first task and writes nothing. Before the fact step the run checks the project's disk share and
refuses to start if the projected size does not fit.

Conf `{"fiscal_year": 2025, "label": "verify-YYYYMMDD"}` is a verification run. Databases go to `.tools/{staging,dimensions,facts}/backfill-runs/<label>/fy2025.duckdb` because the loaders refuse other paths, ER output and work files to `.tools/backfill-runs/<label>/`, and evidence to `docs/evidence/task09-backfill-airflow-<label>/`. It reads the canonical employer registry and never publishes to it. Backfill steps run inside the scheduler's 4 GB memory limit while DuckDB may use 4 GB, so raise it first: `systemctl --user set-property --runtime sponsoratlas-airflow-scheduler.service MemoryMax=8G`.

Results:
[`docs/evidence/task09-backfill/`](../docs/evidence/task09-backfill/) (DAG run, 2026-10-28),
[`docs/evidence/task04-er-threshold-0875/`](../docs/evidence/task04-er-threshold-0875/) (CLI rerun
from `er_export`, 2026-10-29) and
[`docs/evidence/task09-backfill-airflow-verify-20261106/`](../docs/evidence/task09-backfill-airflow-verify-20261106/)
(verification DAG run `manual_backfill_verify_20261106`, label `verify-20261106`, FY2025,
2026-11-06 05:50 to 05:54 UTC; all 10 steps SUCCEEDED).
