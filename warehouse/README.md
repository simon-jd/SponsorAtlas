# Snowflake foundation

Terraform for the Snowflake account objects, using the `snowflakedb/snowflake` provider 2.22.0
(lock file committed) and Terraform 1.13.5 from `.tools/terraform/`. Applied on 2026-10-30 to
a Snowflake trial account with `native_phase=READY` (91 objects, no drift; local Terraform
state, git-ignored). The warehouse runs under a resource monitor that never resets and
suspends at 80% / immediately at 100%; its quota was 5 credits and is 20 since 2026-10-30
(ends 2026-11-24 00:00 UTC). The live grant and policy tests pass on that account (below).

| Object | Configuration |
|---|---|
| Databases / schemas | `RAW.{LCA,USCIS,BLS,JOBS}`; `ANALYTICS.{STAGING,INTERMEDIATE,MARTS}`, all managed access |
| Warehouse | `SPONSORATLAS_XS`: X-Small, single cluster, `auto_suspend=60`, created suspended, 300 s statement timeout |
| LOADER / `AIRBYTE_SVC` | create and write tables in the RAW schemas |
| TRANSFORMER / `DBT_SVC` | read RAW; create and write in the ANALYTICS schemas |
| ANALYST / `CUBE_SVC` | read MARTS tables and views only; no RAW, STAGING or INTERMEDIATE access |
| PII_READER | role only, for the masking policies in `governance/`; no user and no data grants |

No role inheritance, `ALL PRIVILEGES`, grant options or account-level grants; grants cover
existing and future objects. Service users are key-pair only (an authentication policy allows
KEYPAIR and nothing else) with secondary roles disabled. `bootstrap/` holds an inert,
comment-only sketch of the manual first-time setup (resource monitor, warehouse import).

## Manual setup (outside Terraform)

These steps were run by the account owner as ACCOUNTADMIN; Terraform does not manage them,
so `terraform plan` shows no drift for them.

- **Resource monitor** `SPONSORATLAS_BOOTSTRAP_RM` (sketch in `bootstrap/`): 20 credits,
  frequency NEVER, suspend at 80%, suspend immediately at 100%, ending 2026-11-24 00:00 UTC.
  It caps warehouse compute only; serverless automatic clustering is billed separately.
- **PUBLIC revokes** (2026-10-30). A new trial account grants objects to PUBLIC that
  Snowflake creates itself, so they are not in Terraform and Terraform cannot remove them:
  `SNOWFLAKE_LEARNING_ROLE` was granted to PUBLIC (USAGE and OPERATE on
  `SNOWFLAKE_LEARNING_WH`, which has no resource monitor, and CREATE SCHEMA on
  `SNOWFLAKE_LEARNING_DB`), and PUBLIC had USAGE on `SYSTEM$STREAMLIT_NOTEBOOK_WH`. Both were
  revoked (`REVOKE ROLE SNOWFLAKE_LEARNING_ROLE FROM ROLE PUBLIC`,
  `REVOKE USAGE ON WAREHOUSE SYSTEM$STREAMLIT_NOTEBOOK_WH FROM ROLE PUBLIC`). Since then every
  service user sees only `SPONSORATLAS_XS` and holds only its own role plus PUBLIC;
  `test_live_account_defaults.py` checks this for AIRBYTE_SVC, DBT_SVC and CUBE_SVC.
- **Temporary identities** (2026-10-30), key pair only, bound to the same authentication
  policy, no grant options:
  - policy acceptance (`test_policies.py`): users `SPONSORATLAS_POLICY_{OWNER_ANALYST,
    NON_OWNER,OWNER_PII,PII_NON_OWNER}` with role ANALYST, two of them with the owner role
    `SPONSORATLAS_OWNER_POLICY_TEST` and two with PII_READER;
  - cost work (`scripts/query_profile_report.py`, `semantic/scripts/cost-refresh.cjs`):
    `COST_OBSERVER_SVC` / COST_OBSERVER (reads ACCOUNT_USAGE through the SNOWFLAKE database
    roles USAGE_VIEWER and GOVERNANCE_VIEWER) and `COST_EXPERIMENT_SVC` / COST_EXPERIMENTER
    (the same views, plus USAGE/OPERATE/MODIFY on `SPONSORATLAS_XS`, MONITOR on the resource
    monitor, CREATE SCHEMA on ANALYTICS and SELECT on six public marts for cloning).

  The account owner removes these identities after the runs, which also leaves PII_READER
  without users again; this was done on 2026-11-01 for the four policy users, the owner role
  and COST_EXPERIMENT_SVC / COST_EXPERIMENTER (COST_OBSERVER_SVC stays for later cost reports).
  A dbt rebuild of the marts (CREATE OR REPLACE) drops the six SELECT grants COST_EXPERIMENTER
  needs; only ACCOUNTADMIN can grant them again.

## Offline checks

```bash
TF_PLUGIN_CACHE_DIR="$PWD/.tools/cache" CHECKPOINT_DISABLE=1 \
  .tools/terraform/terraform -chdir=warehouse/terraform init -backend=false -input=false
.tools/terraform/terraform -chdir=warehouse/terraform validate
CHECKPOINT_DISABLE=1 .tools/terraform/terraform -chdir=warehouse/terraform test   # mock provider, plan only
.venv/bin/python -m pytest warehouse/tests                                         # part of make test
```

`terraform init` downloads the provider, so the Terraform commands are not part of `make test`.

## Live checks

`RUN_SNOWFLAKE_GRANTS=1` runs `warehouse/tests/test_grants.py`,
`warehouse/tests/test_live_marts_access.py` and `warehouse/tests/test_live_account_defaults.py`.
Last run 2026-10-31 on the trial account: **8/8 PASS** (3 + 3 + 2, one of them the offline
helper check) ([log](../docs/evidence/task01-snowflake-grants-20261031/pytest-live.log)), with the
deploy identity as ADMIN. `RUN_SNOWFLAKE_POLICIES=1` runs `test_policies.py` with the policy
identities above: **5/5 PASS** on 2026-10-31
([log](../docs/evidence/task08-snowflake-policies-20261031/pytest-live.log)). `test_grants.py` creates
and clean up UUID-named transient objects and check that ANALYST cannot read RAW, can read MARTS
but not insert, and that TRANSFORMER can read RAW but not write it. `test_live_marts_access.py`
checks on the real dbt tables that CUBE_SVC reads every mart but no RAW, STAGING or
INTERMEDIATE relation, that the default build has no policy references (the only consumer
in code, `fact_my_applications`, is disabled by default), and, on UUID-named probe tables
built with the dbt macro, that the row access policy hides the row from ANALYST and
TRANSFORMER and the masking policy masks the email for ANALYST without PII_READER. They need
`SNOWFLAKE_TEST_ACCOUNT`, an admin user/role/key file and one key file per service user
(`SNOWFLAKE_TEST_{ADMIN,LOADER,TRANSFORMER,ANALYST}_PRIVATE_KEY_FILE`); without them they skip.

Provider credentials come only from environment variables (`SNOWFLAKE_ORGANIZATION_NAME`,
`SNOWFLAKE_ACCOUNT_NAME`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_PRIVATE_KEY`); passwords
and OAuth tokens are not supported. Service public keys go into a local, git-ignored
`terraform.tfvars` (see `terraform.tfvars.example`).
