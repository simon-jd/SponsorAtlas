# SponsorAtlas Cube semantic API

The Cube semantic layer over the public marts, with two sources: validated local Parquet and explicit opt-in trial Snowflake. It runs the real Cube 1.8.0 REST gateway and DuckDB 1.5.5, with an independent Python DuckDB 2.0 SQL oracle. Snowflake and Azure deployment have not been executed.

Use project Node **24.22.1** (npm **11.19.1**) at `.tools/node-runtime/node-v24.22.1-linux-x64/bin`. The parent has already verified the [official Node archive](https://nodejs.org/dist/v24.22.1/) against SHA256 `6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6`. The Python commands use the existing project `.venv` (DuckDB 2.0.0). No global runtime changes are needed.

From the repository root:

```sh
semantic/run.sh install
semantic/run.sh test fixture
semantic/run.sh test actual
.venv/bin/python -m unittest semantic.tests.test_export -v
.venv/bin/python semantic/scripts/check_protection.py
semantic/run.sh clean
```

The exact npm lock is owned here. `.npmrc` disables lifecycle scripts; `install_native.py` installs only the required official Cube 1.8.0 Linux x64 glibc fallback native extension, checking the release archive SHA256 and extracted binary SHA256. The archive digest was verified against the [official release asset metadata](https://api.github.com/repos/cube-js/cube/releases/tags/v1.8.0). Subsequent installation uses the project cache. No Cube Store executable is downloaded or started. Local Parquet rollups use `external: false`; the trial Snowflake branch uses `external: true` and a separate bounded local DuckDB external driver. The API identity never creates source tables. Cube's generic “Cube Store is not found” startup warning remains visible. Other platforms need a separately verified native artifact.

`prepare.py actual` opens the canonical full-year facts database (`scripts/canonical_paths.py`) with `read_only=True`; it exports only three field-allowlisted projections of public facts with unique many-to-one occupation/geography/date lookups. No employer current-attribute join, RAW payload, applications fact, personal data, coordinates, or source employer name is exposed. Its manifest records source/table, counts, SHA256, sizes, modification times, version and actual/fixture provenance. `prepare.py fixture` creates a separate in-memory `ANALYTICS_TEST` schema; every row is synthetic. Neither mode changes upstream facts or dimensions.

To run the API manually, first prepare a snapshot, then set a strong server-side secret through the environment and run:

```sh
semantic/run.sh prepare actual
# CUBEJS_API_SECRET must already be set by the server environment.
semantic/run.sh serve parquet actual
```

It binds **127.0.0.1:4000** by default (`PORT` can override the port). Only authenticated `GET /cubejs-api/v1/meta` and `POST /cubejs-api/v1/load` are routed to Cube; JWTs use the server secret. There is no browser secret, SQL/GraphQL/playground route, SQL wire listener, or unrestricted source query endpoint. Tests generate expiring JWTs in memory; they never write tokens to logs. A query uses one fact namespace or one view at a time, so different grains cannot be silently blended.

Example request body for an authenticated POST to `/cubejs-api/v1/load`:

```json
{"query":{"measures":["wages.oews_median","wages.estimate_count"],"filters":[{"member":"wages.soc","operator":"equals","values":["15-1252"]},{"member":"wages.area","operator":"equals","values":["1:99"]},{"member":"wages.survey_year","operator":"equals","values":["2025"]}]}}
```

Do not replace snapshot files while a service runs. Stop it, prepare the replacement, and run `semantic/run.sh clean` before restarting; a changed snapshot with old scratch is rejected by the startup manifest fingerprint. `clean` removes only `.tools/semantic/{fixture,actual}/scratch`, preserving source Parquet and JSON/log evidence. Every test starts with fresh scratch, builds rollups on demand, checks REST usage and persisted rollup rows, and terminates its Cube child with exit 0. Manual services stop on SIGTERM. Refresh scheduling is disabled, concurrency is one, Node heap is 1 GiB, DuckDB is 512 MB/one thread/256 MB spill. No persistence across future ACA scale-to-zero is assumed; cold rebuilding is the local behavior, not an ACA acceptance claim.

Parquet files are 0444. The actual writable Cube database and preaggregations live under `scratch/`; DuckDB external access is disabled except for the three explicit input paths. Startup refuses incorrect resource settings and tests actual source-overwrite, OS write-open, and arbitrary-file-read denial. These are accidental-write and API exposure boundaries under the current unprivileged account, not isolation against an administrator or a compromised same-user process that can change file permissions. Kernel-disabled user namespaces prevented a `bwrap` sandbox here. The original marts database is never passed to Cube.

Metric definitions, hand calculations, actual results and remaining gates: [semantic-metrics.md](../docs/dev/evidence/semantic-metrics.md).


The source lifecycle is explicit. Before a real trial, `serve parquet actual` uses the protected local snapshot. During the real trial, `serve snowflake` selects live `ANALYTICS.MARTS`; after exporting and validating a snapshot **before** the trial cutoff, stop Snowflake serving and explicitly select Parquet with `SEMANTIC_SNAPSHOT_DIR`. There is no automatic fallback, invented trial date, post-expiry refresh, or assumption that a local cache survives future ACA scale-zero. Blob provisioning/upload belongs to Task15/16 after the required price/budget ledger; these commands create no cloud resource.

The Snowflake branch requires real server-side settings from `.env.example`: `SEMANTIC_SNOWFLAKE_OPT_IN=1`, account, owner-only RSA private-key file (at least 2048 bits, optional key encryption passphrase), and explicit UTC trial start/expiry with positive duration ≤30 days. User/role/warehouse/database/schema are fixed to **CUBE_SVC/ANALYST/SPONSORATLAS_XS/ANALYTICS/MARTS**; conflicting overrides, missing credentials, passwords, invalid trial dates and expired trials fail closed. Account passwords are never supported. The required [Cube key-pair interface](https://docs.cube.dev/admin/connect-to-data/data-sources/snowflake) and [Snowflake RSA authentication](https://docs.snowflake.com/en/user-guide/key-pair-auth) are implemented, not merely listed dependencies.

```sh
# After manually supplying real credentials and real trial timestamps in the server environment:
semantic/run.sh verify-snowflake
semantic/run.sh serve snowflake
# Run while the trial is still active and safely before cutoff; choose a new snapshot name:
semantic/run.sh export-snowflake pre-expiry
# Stop the Snowflake process, then serve the exact validated export without contacting Snowflake:
SEMANTIC_SNAPSHOT_DIR="$PWD/.tools/semantic/snowflake-export-pre-expiry" semantic/run.sh serve parquet actual
```

A seven-minute pre-expiry cutoff rejects new source queries and cached REST loads, and stops the server. Source statements are capped at 240 seconds with queued timeout 60 seconds, secondary roles disabled, detached queries aborted, session keepalive off, and warehouse auto_suspend=60 checked rather than altered. This gives a shutdown/idle margin before the declared expiry. Startup verifies the actual user/role/warehouse, single-cluster X-Small size/auto_suspend=60, unique fact/dimension keys and projection row accounting. Failure causes no service listener or silent fixture fallback. The real remote settings and shutdown behavior still require the opt-in remote run; no credential-free check is represented as remote billing/deployment acceptance.

`source.cjs` uses the real Snowflake1.8.0 driver with `readOnly:true`. Its `query`, `downloadQueryResults`, `memory`, and `stream` paths all enforce cutoff and fixed public-source SELECTs; the guard preserves the driver's cancellable promise. The only non-SELECT operations are fixed session control (`USE SECONDARY ROLES NONE`, session timeout/detach settings) and scoped `SHOW WAREHOUSES` metadata. `projections.json` is shared with the local exporter; Cube's source-aware repository substitutes the same public projections for Snowflake and retains all metric/dimension definitions. All fact joins remain left joins with uniqueness/accounting checks; no current-employer, RAW, payload, personal-fact or coordinate data is queried.

For Snowflake rollups, fixed Cube1.8 selects `refreshReadOnlyExternalStrategy`: it downloads the aggregate SELECT via `downloadQueryResults` and uploads rows only to `externalDriverFactory`'s independent DuckDB `external-scratch/preaggregations.duckdb`. The external driver has no source-file access. There is no source-schema creation, source-table write, role escalation, DBT refresh credential or Cube Store. The fiscal-year count/numerator rollups stay additive; exact percentiles still use full source values. Local cache loss causes on-demand cold rebuilding while the trial is active; after cutoff no rebuild/query is allowed.

`verify-snowflake` is a real remote REST/source-SQL check of seven metrics, five-dimensional full grouped hashes, three views and two cold external rollups. It creates a fresh local work directory, saves no JWT/key/passphrase, checks source projection hashes before/after and closes both source and Cube connections. Every comparison is exact: counts and quantiles as returned, the certified rate as integer numerator / denominator (Snowflake returns `1.0 * SUM / COUNT` at NUMBER scale 6, e.g. `0.977779`, which is accepted only as the exact ratio rounded to that scale). With `SEMANTIC_LOCAL_ORACLE=.tools/semantic/actual/oracle.json` it also requires the local DuckDB oracle to equal the Snowflake SQL; `SEMANTIC_SNOWFLAKE_DEBUG=1` prints the failing check's message. It passed on a Snowflake trial account on 2026-10-31 (`docs/evidence/task07-cube-snowflake-20261031/`). `export-snowflake` reads only fixed public projections using the installed Python Snowflake/Arrow stack, writes bounded Parquet (≤1 million rows per table, ≤128 MiB snapshot), checks live-source hashes before/after, compares seven metrics against independent source SQL using the real Cube DuckDB1.5.5 engine, and publishes a new manifest only after validation. Failed exports remove pending output; existing snapshots are never overwritten. The Parquet service rejects Snowflake-export manifests missing the native seven-metric validation or matching file checksums.

Offline source verification is separate:

```sh
semantic/run.sh test-source
```

It checks real Cube Snowflake SQL compilation (no Snowflake connection), targeted server startup failures, native snapshot acceptance/rejection, and a **synthetic cross-driver mechanism** using a physical native READ_ONLY DuckDB source plus a second real DuckDB cache. Genuine Cube REST cold-builds/hits the two rollups; exact percentile queries bypass them; native source DDL fails; source/input SHA/size/mtime stay unchanged. All real drivers are released. Node's test-force-exit clears remaining integration-harness timers after explicit cleanup; it does not stand in for connection release. This mechanism is not a Snowflake end-to-end observation.
