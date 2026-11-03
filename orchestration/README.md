# SponsorAtlas orchestration

The daily `sponsoratlas` DAG uses Airflow **3.3.2**, Cosmos **1.15.1**, and the existing native dbt **1.12.6** environment. `requirements.lock` pins the isolated Airflow dependency set, resolved against the official Airflow Python 3.12 constraints. No root dependency lock is changed.

```bash
uv venv --no-cache --python .venv/bin/python .tools/orchestration/venv
uv pip sync --no-cache --python .tools/orchestration/venv/bin/python orchestration/requirements.lock
orchestration/run.sh airflow db migrate
orchestration/run.sh pytest -q orchestration/tests
.tools/node-runtime/node-v24.22.1-linux-x64/bin/node --max-old-space-size=768 --test --test-concurrency=1 --test-force-exit orchestration/tests/cube_revision.test.cjs orchestration/tests/cube_external_refresh.test.cjs
```

`orchestration/run.sh` scopes Airflow state, metadata, logs, DAGs and dbt artifacts to this project. It uses `LocalExecutor` (parallelism two; the public DAG permits one task and one run at a time), disables examples, telemetry and Cosmos metastore/partial-parse caching. Rendering uses credential-free native `dbt ls`; execution uses the orchestration wrapper around the existing native dbt CLI. Each task has two retries and a 15-minute execution timeout. Each model/seed/snapshot runs `dbt build --indirect-selection cautious`; the final test task explicitly selects all public tags and the thirteen reviewed singular gates. This final selection includes all thirteen fact foreign keys, enforced model contracts, grain, accounting, dimension and legacy staging gates. Private application models remain disabled.

## Production opt-in

Fill the exact endpoints, connection ID, credentials, trial timestamps and notification destination in `config.production.env.example` using secure runtime configuration. An unfilled configuration fails at runtime. Real Airbyte/Snowflake/Slack or SMTP has **not** been accepted locally. Airbyte connections must be **Manual**, so Airflow is their only scheduler. The Airbyte connector/source/RAW and reviewed `DIMENSIONS_INPUT` / `FACTS_INPUT` mapping contracts must already exist; this task does not invent upstream ER or full-year mappings.

Use a real production metadata database and a durable shared `SPONSORATLAS_AIRBYTE_STATE_DIR` accessible to all workers. The local SQLite rehearsal is a development deployment. Keep service identities separate: native dbt uses DBT_SVC/TRANSFORMER and `SNOWFLAKE_PRIVATE_KEY_FILE`; Cube uses CUBE_SVC/ANALYST and `SPONSORATLAS_CUBE_PRIVATE_KEY_FILE`. Both use JWT/key pairs and SPONSORATLAS_XS. Cube's existing preflight retains XS/auto_suspend=60 and the public-mart allowlist.

The wrapper rejects missing, inactive or >30-day trial windows before cloud dispatch. Admission requires enough time for native dbt's bounded 840-second process plus cancellation/cleanup before the seven-minute trial safety margin. It recomputes `min(840, cutoff-now-60)` immediately before spawning, detects late returns, and adds session statement240s/queue60s/abort-detached controls to native Snowflake connections. It never changes the global profile. Airbyte polling leaves 60 seconds of its 900-second total budget for DELETE cancellation and confirmation; unresolved cancellation is persisted, alerted and blocks retry until reconciled.

Every `(endpoint, connection)` has an exclusive advisory lock and durable active authority, with a separate audit record for every run. Workers must share a filesystem supporting POSIX flock, atomic rename and directory fsync. State files are created0600; initial directories and every replacement are fsynced. Both active authority and per-run audit commit **before** the first token request/POST. Never unlink the persistent `.lock` inode. Unresolved `trigger_pending`, submitted or `cancel_unconfirmed` blocks **all later run IDs**, even after the Airflow run finishes. When the connection index is absent, matching old per-run unresolved records are adopted conservatively; multiple unresolved legacy records require reconciliation.

New work alone requires960s trial admission. A known job can be recovered near cutoff solely within its original900s total and absolute trial cutoff; close windows enter same-job cancellation. Real urllib timeouts remain typed and redacted. Any known-job transport failure enters at most60s cleanup; terminal responses after cutoff cannot confirm success. Unconfirmed cancellation is durable and blocks new POST. Confirmed terminal results are committed and cached; a failed/cancelled run never creates a replacement job. A later run may acquire triggering authority only after confirmed termination.

For a lost POST response or unresolved job, first inspect the exact Airbyte connection/job history. Reconciliation must hold the same connection flock and durably update **both active authority and its per-run audit** with the verified job ID/terminal result using the state commit interface. Unknown outcomes remain blocked. Never delete pending state/index or manually mark a guessed terminal result to force retry.

## Controlled Cube maintenance

`semantic/scripts/refresh.cjs` holds an exclusive per-work lease shared with `semantic/server.cjs`. Stop the gateway before maintenance. A live gateway or second refresh is refused; stale ownership after SIGKILL requires explicit reconciliation of that exact process before removing `work-owner.json`. No PID guessing, kill-by-name, public admin route, or expanded Snowflake write permissions is used.

The worker creates a validated random revision, invokes a private loopback Cube and loads only the two counts rollups with top-level REST `cache: "must-revalidate"`. It shares one source driver between preflight and execution, closes every driver, independently reopens the physical file READ_ONLY and verifies both new rollups and counts, then atomically promotes the owner-only ready state. A failed build retains the preceding ready revision. Restart the gateway with the **same SEMANTIC_WORK** to reuse those physical tables. The maintenance worker must be the sole owner of that work directory; external DuckDB storage is never written concurrently with a serving process. Directory permissions are tightened to 0700 and revision/ownership files are 0600.

For the explicit local synthetic mode, the Cube task exports a **new** immutable public projection from the separate orchestration dbt database, checks dimension/fact uniqueness and projection row counts, and reads its SHA256 manifest. It never modifies the protected Task6 database, original observation dates, or public projection definitions. Production refresh instead reads live public Snowflake marts directly through the existing read-only driver.

## Local rehearsal and stopping

`python -m orchestration.sponsoratlas_orchestration.demo prepare` must use the root `.venv` and creates a new labelled synthetic DB; it refuses overwrite. `demo serve --port 18789` is a synthetic HTTP transport, not Airbyte or Slack. Configure explicit `synthetic_http`, local target, parquet source, local API credentials and local_http alert URL before running a rehearsal. These fixtures are never a production fallback.

After migration, `python -m orchestration.sponsoratlas_orchestration.supervise` in the isolated environment starts a loopback execution API (18780), DAG processor and scheduler with network log serving disabled. It owns the complete process trees, samples aggregate RSS, stops above 8GB/1200s, and reaps its processes on SIGINT/SIGTERM or when `.tools/orchestration/STOP` appears. Remove that marker explicitly before a new run. Stop the separate synthetic HTTP sink too. `SPONSORATLAS_ENABLE_DEADLINE_PROBE=1` enables a separate scheduler acceptance DAG with a five-second deadline and 25-second task; it is absent by default.

The production DAG's 30-minute `DeadlineAlert` uses **DAGRUN_QUEUED_AT**, so historical logical dates do not immediately trigger runtime alerts. This Airflow 3 experimental API replaces the old post-completion SLA detection with running-DagRun monitoring. Source freshness is separate: each public job stream's maximum `_airbyte_extracted_at` must be within 24 UTC hours.

## Fiscal-year backfill

Employer IDs survive a rebuild: the `er_predict` step runs `er.registry_archive`, which inherits the latest registry in `.tools/er/registry-archive-fy<year>` and archives the new one there (files named by SHA-256, `LATEST` names the newest). A merge, split or retraction stops the step with `IDENTITY_REVIEW_REQUIRED` (exit 3) for review. `backfill run --initialize-registry` starts new employer IDs instead (decided 2026-10-29).

```bash
# Read-only: are all four quarters archived, SHA-matched and registered as dbt sources?
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill readiness --fiscal-year 2025
# Rebuild RAW load -> staging -> ER -> dimensions -> facts into the canonical local databases
# (destinations must not exist; --from-step <step> reruns one step and everything after it).
.venv/bin/python -m orchestration.sponsoratlas_orchestration.backfill run --fiscal-year 2025 \
  --output <summary.json> --logs <dir>
```

The manual `sponsoratlas_backfill` DAG runs the same steps as Airflow tasks (conf
`{"fiscal_year": 2025}`) and writes each step's timing JSON and log to
`docs/evidence/task09-backfill/`. Before the fact step the run checks the project's disk share and
refuses to start if the projected size does not fit. Results:
[`docs/evidence/task09-backfill/`](../docs/evidence/task09-backfill/) (DAG run, 2026-10-28) and
[`docs/evidence/task04-er-threshold-0875/`](../docs/evidence/task04-er-threshold-0875/) (CLI rerun
from `er_export`, 2026-10-29).
