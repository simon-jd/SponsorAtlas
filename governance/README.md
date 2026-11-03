# SponsorAtlas private governance

Two parts: a private applications fact with owner-based row access and email masking, and a local OpenMetadata 2.0.6 catalog with column lineage built from the dbt artifacts. The Snowflake row access and masking policies are written and tested offline only; real personal CDC and Snowflake catalog ingestion have not been run. Latest lineage results: [`docs/evidence/task08-lineage-fy2025/`](../docs/evidence/task08-lineage-fy2025/).

## Application contract v2

`ingestion/postgres/demo_schema.sql` and the optional synthetic CDC probe now carry `application_id`, `owner_role`, `contact_email`, and `contract_version=2`. `id` remains the event primary key. Production source migration must obtain these values from the real application system; neither event ID nor job ID can supply application identity, and unknown owners must never be backfilled by guessing. The current real source is unconfirmed. The older Task3 staging model and its fixture/archive schema remain unchanged.

- One `application_id` identifies one application, potentially with many events. Two applications may reference the same job. Owner and job are immutable across an application's events. Reassignment requires a separately reviewed contract migration.
- Each event is a full snapshot, with a nullable email. Order active events by `event_at DESC`, numeric CDC LSN descending, then event ID descending. A corrected event uses its stable event ID; duplicate raw event IDs fail validation.
- `_ab_cdc_deleted_at` retracts **that event only**. Another retained event may become latest. If all events are removed, no application row remains. It never implicitly deletes a different application.
- `application_deleted` is an explicit retained application tombstone; if latest, it excludes the application. A newer legitimate event reopens it. Do not physically retract a lifecycle tombstone if the intended action is permanent application deletion. This behavior is contractual, not inferred from CDC.
- Owner roles must be uppercase `SPONSORATLAS_OWNER_*`, and also occur in the explicit dbt `approved_application_owner_roles` list. Its default is empty. Terraform's `application_owner_roles` must match that approved list. New owner roles receive **no automatic user or service grants**. A reviewed grant must assign the correct owner role to its actual owner. `CUBE_SVC`, `ANALYST`, and `PII_READER` receive no owner assignment.
- `insert`/`update` statuses are reserved synthetic Task2 probe statuses; business statuses are `submitted`, `interview`, `offer`, `rejected`, `withdrawn`, `application_deleted`.

Enable the isolated branch explicitly; it is outside the old `staging` directory and has no public `facts` tag:

```bash
.venv/bin/python -m governance.fixture .tools/governance/synthetic_applications.duckdb
SPONSORATLAS_DUCKDB_PATH="$PWD/.tools/governance/synthetic_applications.duckdb" \
  .venv/bin/dbt build --project-dir dbt --profiles-dir dbt \
  --select tag:private_applications \
  --vars '{enable_private_applications: true, approved_application_owner_roles: [SPONSORATLAS_OWNER_SYNTHETIC, SPONSORATLAS_OWNER_OTHER]}' \
  --target-path "$PWD/.tools/governance/dbt-artifacts"
```

Fixture creation refuses an existing destination. It uses only fictional `synthetic-*` IDs and `example.invalid` addresses. Run `build`, which checks the source contract before the fact; `run` alone omits dbt data tests. No export allowlist was expanded. Cube and Blob retain Task7's three public facts only.

## Snowflake policy lifecycle

`warehouse/terraform/policies.tf` defines versioned owner RAP, email masking policy, `PII` and `SENSITIVITY` tags. The owner column and masked email are separate signatures. The RAP requires the row's owner role to be active through the real primary/secondary role hierarchy. `PII_READER` does not grant ownership or bypass RAP; an owner without it receives `***MASKED***`, while null email stays null.

The Snowflake custom dbt materialization issues exactly one `CREATE TABLE ... WITH ROW ACCESS POLICY ... AS (...)`, or `CREATE OR REPLACE ... COPY GRANTS ...` for every subsequent run/full refresh. It binds column masking and tags in that same DDL. It creates no temporary table, backup, clone, unprotected view, or post-hook interval. Hooks and replacing a non-table fail closed. Replacement failure leaves the previous protected relation in place. The local DuckDB materialization has no security-policy semantics.

The rebuild reads restricted STAGING, never the protected target. Consequently TRANSFORMER sees zero fact rows and no MERGE exemption is needed. RAW and private STAGING are processing inputs restricted to LOADER/TRANSFORMER by the existing grant graph; those trusted operators necessarily handle source PII. The fact's query policy is not a claim that privileged administrators cannot alter security objects.

TRANSFORMER receives only object-specific APPLY on these policies/tags. No global APPLY or grant option is added. For a policy signature/name change: create a new version, grant its scoped APPLY, update the materialization, atomically rebuild all consumers with the new version, inspect Snowflake `POLICY_REFERENCES`, then explicitly retire the old policy. `prevent_destroy` blocks accidental Terraform removal of live v1 policies. Never detach a policy to refresh a table. No Terraform apply was performed.

Live tests need Enterprise or higher, the deployed policies/grants, and six **distinct** key-pair users. Set `RUN_SNOWFLAKE_POLICIES=1`, `SNOWFLAKE_TEST_ACCOUNT`, `SNOWFLAKE_POLICY_OWNER_ROLE`, and for each `ADMIN`, `TRANSFORMER`, `OWNER_ANALYST`, `NON_OWNER`, `OWNER_PII`, `PII_NON_OWNER`, set `SNOWFLAKE_POLICY_<identity>_{USER,ROLE,PRIVATE_KEY_FILE}` (optional `_PRIVATE_KEY_PASSPHRASE`). Owner analyst must have primary ANALYST plus the approved owner secondary role, without PII_READER; owner PII has both; non-owner and PII-only identities lack ownership. The fixture checks actual session role/user/membership, verifies XS/60, creates/replaces a protected synthetic table using the exact dbt macro, verifies nonempty masked rows and separate zero-row sessions, and drops its own table in `finally`. Missing explicit opt-in skips; once opted in, missing settings fail. These tests never create accounts or grant owner roles.

```bash
.venv/bin/pytest -q warehouse/tests/test_policies.py
```

`governance.remote_ingest` separately requires `RUN_SNOWFLAKE_METADATA=1`, the documented environment variables in its YAML, a readable private key, and three real Snowflake artifacts. It disables persisted service connections (`storeServiceConnection: false`), checks XS/60, requires a Snowflake manifest and successful `target=snowflake` results, creates the declared governance metadata team, then runs metadata and dbt workflows. It refuses local artifacts. No passwords or profiler/sample pipelines are used. Keep its key file out of metadata configuration and logs; it is read into memory only.

## Real local OpenMetadata

Dependencies are isolated in `governance/requirements.in`, its exact lock, and ignored `governance/.venv`:

```bash
uv --no-cache venv governance/.venv --python .venv/bin/python
uv --no-cache pip sync governance/requirements.lock --python governance/.venv/bin/python
```

The project uses the official [OpenMetadata 2.0.6 distribution](https://github.com/open-metadata/OpenMetadata/releases/tag/2.0.6-release), SHA256 `7a3daba784adc163b2e63ba4609679a34edd1d9fb5915605a81d8541643bbf0c`, and official [Elasticsearch 9.3.0 archive](https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-9.3.0-linux-x86_64.tar.gz), checked against the vendor's `.sha512`. Extract them into `.tools/governance/openmetadata-2.0.6` and `.tools/governance/elasticsearch-9.3.0`. Always re-audit disk **before** downloading/extracting. The archive byte counts are 399,599,165 and 665,809,157; unpacked totals are 431,830,052 and 1,293,416,029. Remove verified download archives to preserve the 18GB project cap.

`compose.yaml` pins official PostgreSQL and Temurin JRE images by digest, mounts these distributions, labels the isolated network/containers, publishes only loopback ports, caps CPU and memory (512MiB + 2GiB + 4GiB, no swap), and limits PostgreSQL spill and Docker/server logs. It creates no Airflow, named volume, cloud resource, or image build. A preliminary bare Java21 download was verified then removed once the container runtime was selected. Do not reinstall removed Task7 Cube/Spark preparations.

```bash
governance/.venv/bin/python governance/prepare_runtime.py
# Refuses replacing existing project secrets. Then:
docker compose --env-file .tools/governance/.env -f governance/openmetadata/compose.yaml up -d postgres elasticsearch
# Wait for PG and ES health, then migrate only this isolated DB:
docker compose --env-file .tools/governance/.env -f governance/openmetadata/compose.yaml run --rm --no-deps \
  -e JAVA_TOOL_OPTIONS='-Xms256m -Xmx1536m -XX:ActiveProcessorCount=2' \
  --entrypoint /runtime/bootstrap/openmetadata-ops.sh server migrate
docker compose --env-file .tools/governance/.env -f governance/openmetadata/compose.yaml up -d server
```

For a new installation, `governance/.venv/bin/python -m governance.initialize_catalog` replaces the local bootstrap administrator password through the local admin API, creates the dedicated viewer with `POST /users` and explicit non-admin/non-bot flags, verifies a password login and matching identity, and then registers catalog schemas. It checks existing viewer credentials instead of treating their presence as successful setup. A failure stops before registration and requires the explicit repair workflow below. Credentials are generated privately and staged durably before creation; the final mode0600 credential is published only after login succeeds. Pending recovery directories are mode0700 and refuse reuse after partial initialization. Keep the entire runtime ignored. The API token helper uses this installation's existing unique RSA key with a one-hour token, never printed.

The original local viewer was initialized through `PUT /users`, whose OpenMetadata2.0.6 path does not initialize a Basic password. This is a source-supported hypothesis for the retained UI401, not proof of the actual stored state. Do not rerun initialization, registration, migration or ingestion to repair that account. `governance.viewer_auth_runner` is the narrow, separately approved repair entrypoint: fixed localhost/version, provider/SMTP/secure-cookie checks, exact administrator/viewer identities, unchanged role/team/domain assignments, and a bounded readonly projection of that viewer's authentication-presence metadata. It permits a single existing-password repair only if the mechanism is absent or JSON-null and a correctly encoded baseline login returns401. An existing non-null mechanism with a failed login is unresolved and receives no password change. No rotation, grants, user deletion or automatic rollback occurs.

The runner requires a root-approved exact source binding, fixed once identifier, deadline and service window; invoking it is a live operation. This followup's code/synthetic approval does **not** authorize it. Its private receipt preserves credential bytes and original absent/null shape with a PostgreSQL row version, never a password hash or whole row. A pure recovery-SQL builder can prepare a separately reviewed single-field CAS while OM is stopped; there is no recovery execution path. A row-version mismatch refuses restoration. Restoring the authentication field does not undo session, login, audit or version-history effects. Preserve the original401 evidence and its unverified field-clearing observation.

Login uses `POST /auth/login` with a base64-encoded password, the returned Bearer token and OM_SESSION cookie, followed by the authenticated identity endpoint and explicit logout. Sensitive requests use a fixed-origin client with redirects/proxies/retries disabled, bounded responses and errors containing only fixed codes/statuses. Neither API success nor synthetic tests establish native UI lineage: the private email chain and public payload/case-number bridge still require visible inspection in the real UI under a separate browser gate.

`catalog.py` registers only actual dbt catalog relations and column definitions under explicit `CustomDatabase` artifact-only services. There is no pretend DuckDB or Snowflake connector. `ingest.py` executes the official 2.0.6.0 dbt workflow against manifest/catalog/run_results without changing their contents. Unbuilt models in broader manifests cause upstream warnings; verified requested column edges, owners, and errors are checked separately. The 1.12.6 manifests were consumed successfully despite the older version range listed in the dbt documentation.

`governance/dbt-readonly/profiles.yml` can generate catalogs of existing archived DuckDB files with `dbt docs generate --no-compile`, using copied manifests/run_results and new target directories. The 1.7GB fact library is opened read-only, never copied. Use `enable_private_applications: true` for the private catalog so its models remain included.

`bridge.py verify` checks the existing Python loader SQL, identical physical schemas and all-row aggregate hashes across `actual-files.STAGING.stg_lca` and `actual.FACTS_INPUT.lca`; `bridge.py publish` rechecks file/loader SHA and adds the verified pipeline column mappings across distinct services (53 on the full-year files). `verify_lineage.py` reads actual OM API edges and asserts the private email chain, the public LCA payload→case-number chain through that bridge, and the worksite ZIP and ZCTA/place→`county_fips` chains. It does not draw or fabricate lineage.

```bash
.venv/bin/python -m governance.bridge verify
governance/.venv/bin/python -m governance.bridge publish
governance/.venv/bin/python -m governance.verify_lineage
.venv/bin/python -m governance.resource_audit
# End every local session:
docker compose --env-file .tools/governance/.env -f governance/openmetadata/compose.yaml stop
```

See [governance evidence](../docs/dev/evidence/governance.md) for tested results and remaining gates. For restart, the retained isolated data and image digests are sufficient. Never run a global Docker prune or touch another project's data.
