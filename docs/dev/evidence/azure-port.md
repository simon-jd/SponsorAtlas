# Task 16 — ADF → Fabric LCA subject-area port

Local implementation and synthetic validation are complete. **Actual Snowflake versus Fabric RED/GREEN, ADF Copy, Fabric Spark/Delta, Warehouse T-SQL compatibility, tenant permissions, trial capacity, native development experience, metered cost and native performance remain OPEN.** No cloud resource, account, tenant setting, service credential, role or published artifact was created or changed by this implementation.

The required path is Azure Data Factory Copy → Fabric Lakehouse Files → validated Delta staging → **writable Fabric Warehouse**. The Lakehouse SQL analytics endpoint is used only as a source for Warehouse reads. This is not a copy of the three public semantic Parquets, a Power BI query, or a substitution of Fabric Data Factory for ADF.

## Controlled source and model boundary

The input is the complete retained annual JSONL envelope for each of the **Q4 worksheets/releases**, not all fiscal-year filings:

| Release | Archived rows | Original XLSX SHA-256 | JSONL SHA-256 |
| --- | ---: | --- | --- |
| FY2024_Q4 | 120,897 | `23bc116e679e7ff10b9ac947dc4faf7942ae59ef1c3ffb5a10be61c4ae31ba30` | `89818451477136344251a27c9e8dc8b168e7570af9e204de03f2d7b7c582dbab` |
| FY2025_Q4 | 118,580 | `81ebf97d471014b91ae685cb7eb505369d7c80af2de67944ddd7531814976370` | `89b84a07a177997a8e41be2db067e788438707a3d01cd95a7d046bd021e120ae` |

These identities/counts came from existing `.provenance.json` metadata. The 893,685,626 JSONL bytes and original XLSX files were **not scanned, copied, hashed or reloaded locally** for Task 16. The protected facts database and published ER registry were not opened or changed. The full original payload stays in restricted archive files; excluded contact fields are not added to marts.

`plan_bundle.py` consumes the existing provenance, the canonical `dbt/seeds/lca_column_map.csv`, official SOC2018/Census2020/Census2023/CBSA2023-07 reference seeds, a reviewed immutable employer crosswalk export, and a complete captured employer-history export. It produces a source manifest, exact upload list, deterministic notebook-code ZIP and ADF pipeline parameters. It does not upload data, create cloud objects or manufacture missing history. JSONL filenames retain release and original SHA. The manifest separately binds original and derived digests, complete raw header schema, row counts, mapping/code/SQL hashes, reference schemas and digests, registry/model/xref lineage, history export receipt and snapshot timestamp.

Employer history must be exported from `ANALYTICS.SNAPSHOTS.snap_employer` at the same native Snowflake Time Travel timestamp as verification. The implemented exporter is `node azure/fabric/snowflake.cjs <UTC-snapshot> <new-export-directory>`; it performs the existing reviewed preflight, fixed history/lineage/source queries, awaits connection release, then exclusively creates owner-only `history.jsonl` and `receipt.json`. It refuses missing history/lineage and never synthesizes either. The receipt binds exact history bytes, typed column schema and full content, snapshot, actual account/region/user/role, the observed `DIMENSIONS_INPUT.lineage(manifest_json)` and shipped query hashes. Canonical UTF-8 JSONL preserves all history versions, NULLs, exact numbers and UTC microsecond timestamps; nonzero sub-microsecond precision fails rather than rounds. `export-history.sql` documents the column projection; the executable sealed templates are authoritative.

The bundle planner now requires the actual receipt file, includes its immutable path/digest in the upload list, derives registry/model/acceptance fields from it, and verifies history bytes/content against it. Fabric notebook verification repeats artifact checks. Native acceptance independently rereads the pinned native history and lineage and compares them with the receipt before connecting to the target. A locally forged receipt plus matching local hashes cannot establish native identity. `config.identity` and target `port_identity` are explicitly declared bundle metadata; they are never assigned to the native Snowflake observation. Native lineage's original crosswalk digest and the bundle's exported crosswalk JSONL digest are different artifacts and are retained distinctly; full actual fact content checks mapping outcomes. No source DB access or permission changes were performed. Current employer attributes cannot replace capture history.

The Warehouse rebuild includes all 35 columns of the canonical LCA fact, deterministic latest-case selection, occurrence accounting, same-date amendment ambiguity, source provenance, and dependent employer, SOC, county/geography and date dimensions. LCA dependencies use SOC2018 and county dimensions, so unrelated OEWS-specific occupations and BLS-area dimension members are outside this subject. The canonical 2000–2035 calendar contains 13,149 days. Employer links require an interval covering the entire date-only fact day; pre-snapshot and partial-day uncertainty stays a NULL employer key with the canonical reason. Capture time is not source-effective time. Connecticut county bridge uncertainty and county-point coordinate semantics are retained.

Hourly wages multiply by 2080, weekly by 52, biweekly by 26, monthly by 12, yearly by 1, after the canonical decimal(18,2) input conversion. Annual wages persist as decimal(24,2), positions as decimal(38,0). Unknown units/invalid numbers remain NULL. Persisted strings are UTF-8 varchar, timestamps datetime2(6), booleans bit; oversized strings and overprecise history are rejected before truncating casts. Fabric key declarations cannot enforce integrity, so explicit checks reject duplicate grain, fanout, orphan keys and overlapping history. A failed check rolls back the complete Warehouse replacement. Same-batch replacement is idempotent.

ER quality remains whatever the supplied reviewed lineage records. The manifest carries the exact acceptance label and disclosure: **agent weak-reference evaluation is not human-adjudicated ground truth; human quality gates remain OPEN.** No labels or evaluation files were edited or used for tuning.

## Count and canonical three metrics

`verify-snowflake.sql` and `verify-fabric.sql` were the first product files authored. They compute exact latest-case count, `certified_rate`, `wage_p50`, and `wage_p90` from the full fact. The rate is Certified plus Certified - Withdrawn divided by every latest case, and NULL for an empty set. Continuous quantiles interpolate and ignore NULL wages; no grouped median is averaged. **LCA filings are not visa approvals and do not establish that a particular job offers sponsorship.**

Native acceptance requires exact integer counts, equal NULL states, finite numeric metrics and explicit absolute `1e-6`/relative `1e-9` tolerances. It additionally compares typed content hashes and counts for all 35 fact fields and every column of the four dependent dimensions: complete employer history, SOC2018 occupation, Census2020/2023 county geography and the fixed 2000–2035 calendar. Numbers travel from Snowflake as lossless SQL strings; schema-tagged row hashes sort canonical UTF-8 bytes and preserve duplicates. This catches employer-ID changes with identical NULL historical keys/old witnesses/metrics and unused history attribute changes. Original release/year/SHA identity and full occurrence accounting remain required.

Snowflake queries pin one Time Travel timestamp; Fabric replacement, internal checks, target measurement, full-content comparison and metric acceptance execute inside the same Warehouse transaction. COMMIT occurs only after acceptance; failures roll back fact, dimensions and `port_identity`. Read-only verification also owns one transaction. The source Lakehouse is sealed after READY; another manifest requires a different dedicated Lakehouse. Operators must keep READY tables immutable and prevent concurrent notebook/Warehouse loads. SQL synchronization counts for every input are checked before any marts deletion.

## Runnable artifacts and native prerequisites

`azure/adf-pipeline.json` is an ARM template for linked services, Binary datasets and the ADF pipeline **inside an existing authorized project factory**. It uses the factory managed identity for private Blob reads and existing Key Vault access, and a Fabric-tenant service principal **certificate** from a versioned PKCS#12 Key Vault secret for the Lakehouse. The exact file list is copied serially with four DIUs, one concurrent copy, no source deletion and no staging resource. Sink paths are `Files/sponsoratlas/<manifest-sha>/<relative-path>` in the configured Lakehouse. Empty placeholder parameters cannot establish authorization or readiness.

Required before a native attempt:

1. Actual authorized project ADF, private Blob container and existing Key Vault outside protected `rg-mlinfra-westus`, with permitted factory managed-identity reads. Certificate principal, certificate version and NEU tenant/workspace membership must already be authorized. Cross-tenant access is not implied by the Azure Student login. No app registration, certificate creation, role grant or tenant-setting change is included.
2. Observed active Fabric trial capacity, workspace assignment, region/expiry and eligibility; actual Lakehouse and a distinct writable Warehouse in the same intended workspace. No paid F-capacity fallback. Existing required Fabric service-principal/OneLake tenant settings and Contributor/data-plane permissions must be confirmed, not silently enabled.
3. Complete immutable source bundle, native captured history and matched registry lineage; exact Snowflake snapshot within Time Travel retention. Real Snowflake RSA service credentials, active trial, XS `SPONSORATLAS_XS`, `auto_suspend=60`, expected `CUBE_SVC`/`ANALYST` identity and no secondary roles. The chosen fixed reader uses that same identity for export and verification. It additionally requires existing USAGE on ANALYTICS/SNAPSHOTS/DIMENSIONS_INPUT and SELECT on SNAPSHOTS.snap_employer, DIMENSIONS_INPUT.lineage and MARTS.dim_employer, plus the existing public marts/preflight and warehouse access. Missing relations, Time Travel retention or grants fail closed; this code creates/grants nothing.
4. Existing Node/Snowflake dependencies and **optional, not installed here**, `pyodbc` plus Microsoft ODBC Driver 18. Owner-only externally provisioned Entra SQL and Fabric API token files, intended tenant/principal/audience, with at least ten minutes remaining. Local token claim checks are binding checks; the services verify signatures. The runner does not extract browser credentials, acquire tokens, install drivers or execute arbitrary commands.
5. Controller-approved fresh resource plan and Retail Prices API estimate/ledger entry before any native source read, upload, ADF execution or cloud resource. Project estimate must remain ≤ $5.40 before creation under the $6 cap; project RG/tags/TTL/reaper rules remain binding. Template existence is not deployment approval.

After those gates are satisfied, the prepared flow is:

```bash
# Only after separate native read/resource authorization and configured key-pair prerequisites:
SEMANTIC_SOURCE=snowflake SEMANTIC_SNOWFLAKE_OPT_IN=1 node azure/fabric/snowflake.cjs <UTC-snapshot> <new-export-directory>
# Native/export inputs must exist; reference-spec points to the actual receipt file.
python -m azure.fabric.plan_bundle --reference-spec reference-spec.json --output azure-bundle
# Review uploads.json; upload exact sources privately at the listed Blob paths.
# Deploy the child-resource template ONLY into the existing authorized project ADF:
az deployment group create --resource-group rg-sponsoratlas-lab \
  --template-file azure/adf-pipeline.json --parameters @deployment-parameters.json
az datafactory pipeline create-run --resource-group rg-sponsoratlas-lab \
  --factory-name ACTUAL_FACTORY --name CopyLcaSubjectBundle \
  --parameters @azure-bundle/pipeline-parameters.json
# Require successful ADF run receipt before importing/running lakehouse_notebook.py.
# Set BUNDLE_ROOT and EXPECTED_MANIFEST_SHA256 in its parameter cell.
# It validates exact bytes, loads the shipped code ZIP on driver/executors,
# materializes Delta staging and publishes READY last. Never install Spark locally.
AZURE_PORT_NATIVE_OPT_IN=1 python -m azure.fabric.native \
  --config native-config.json --load --bootstrap --output native-first.json
# Repeat same committed source without --bootstrap to check rebuild idempotence.
AZURE_PORT_NATIVE_OPT_IN=1 python -m azure.fabric.native \
  --config native-config.json --load --output native-repeat.json
# Read-only native parity uses neither --load nor --bootstrap.
```

The commands above were **not executed**. `--bootstrap` targets an empty dedicated Warehouse and fails rather than dropping an existing schema. Entra Fabric REST metadata must identify the exact Warehouse item/server; then SQL database identity and UTF-8 collation are checked before writes. Optional-driver absence, authorization failures and target SQL errors yield no successful parity receipt. A future native RED run must execute real validation SQL on an absent/nonmatching native target; the local missing-config rejection is not that RED.

## Retained evidence and development/cost/performance comparison

| Dimension | Local evidence | Actual Snowflake vs Fabric |
| --- | --- | --- |
| Development | Shared canonical annual map; deployable ADF definitions, portable normalization code ZIP, explicit Warehouse SQL and transaction adapter. Authoring needed separate archival, Delta synchronization and Warehouse stages. | Native setup/adapter/permission friction and development duration **unobserved, OPEN**. |
| Correctness | 25 final tests pass; real shipped SQL exercised under declared DuckDB dialect substitutions, alongside independent Python oracles. Operational absent-target RED → rebuilt-target GREEN retained. | Native ADF/Spark/Delta/T-SQL compatibility and cross-engine count/metrics **unobserved, OPEN**. |
| Cost | No new dependencies or cloud actions from this implementation. No metered cost assertion derived from local tests. | Blob/storage/ADF/IR/activity/Key Vault/Spark/warehouse cost and current Retail API quote **unobserved, OPEN**; no paid capacity fallback. |
| Performance | Final 25-test fixture run: 0.558 s monitored elapsed, 102,211,584-byte sampled peak process-tree RSS. Earlier 23- and 24-test runs remain retained. | Native ingestion, notebook, rebuild, exact-quantile latency and comparative throughput **unobserved, OPEN**. These sub-second fixture numbers are not warehouse benchmarks. |

The detailed commands, failures, runtime limits and self-review are in `.superpowers/sdd/4-sponsoratlas-data-frontend-plan/task-16-report.md`. Raw logs and tree-monitor receipts are under `azure/fabric/evidence/`. The initial observed run happened after partial normalization/SQL implementation while runtime authorization was pending: 9/11 passed, one missing-module error and one harness comment-splitting error. That is not retroactively called preimplementation RED for all features. The separate operational RED queried an absent local target; GREEN built actual local adapted SQL and matched 3 cases, rate 2/3, p50 52,000 and p90 60,320 with idempotence. The native entrypoint rejected missing configuration with exit 1 and did not create a native receipt.

Approved local graph: zero downloads/installations/services/Docker/GPU; existing Python, Node and DuckDB only. DuckDB is in memory, one thread, 512 MB memory and 64 MB maximum spill. Every runtime stage uses the reviewed process-tree monitor with 180 s and 2,000,000,000-byte RSS limits; all stages report completed cleanup and no remaining PIDs. The source fixture limit is 100 rows, with a separately approved 13,149-row generated-calendar exception. Fresh pretest point: 15,848,939,418 bytes, 2,151,060,582 bytes headroom against 18e9. Budgeted additions ≤74 MB, or ≤138 MB with optional maximum spill. This point retains the four unlabeled Node base/configs' 660,740,896-byte doubled shared-chain charge plus 1 MB metadata and historical unreadable PostgreSQL 111,861,760-byte charge. It is not a continuous disk-peak or fresh complete PG measurement. Controller final audit remains separate.

## Primary documentation checked

- [ADF Fabric Lakehouse connector](https://learn.microsoft.com/en-us/azure/data-factory/connector-microsoft-fabric-lakehouse): chosen Binary Files connector, Lakehouse linked-service properties, PKCS#12 service-principal certificate and Key Vault reference.
- [Fabric Warehouse T-SQL surface](https://learn.microsoft.com/en-us/fabric/data-warehouse/tsql-surface-area): Warehouse writes, Lakehouse SQL read boundary, NOT ENFORCED keys and transaction-related support limitations.
- [Fabric persisted data types](https://learn.microsoft.com/en-us/fabric/data-warehouse/data-types): UTF-8 varchar, decimal, bit and datetime2 precision constraints.
- [Fabric PERCENTILE_CONT](https://learn.microsoft.com/en-us/sql/t-sql/functions/percentile-cont-transact-sql?view=fabric): aggregate syntax and NULL/interpolation behavior.
- [Warehouse connectivity](https://learn.microsoft.com/en-us/fabric/data-warehouse/how-to-connect), [Entra authentication](https://learn.microsoft.com/en-us/fabric/data-warehouse/entra-id-authentication), [service principals](https://learn.microsoft.com/en-us/fabric/data-warehouse/service-principals) and [trial eligibility](https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial): native prerequisites retained from controller research; not evidence that this tenant is configured.


## Fix round 1: transaction and independent identity acceptance

The native reader adds an Azure-only, private exact-template read policy; the shared semantic public-marts allowlist is unchanged. Callers receive only `read(templateName)`, `preflight()` and `release()`. Fixed queries use the reviewed lifecycle's hydration/cancellation/deadline/pre-dispatch guards and awaited release. Unknown/injected names and extra arguments fail; no driver or arbitrary-SQL method is exposed. No SDK connection is bypassed. Local tests use an offline SDK seam with the actual lifecycle.

Fix evidence includes original failing atomic replacement and incomplete identity tests, then rollback/content/receipt tests, Python–JavaScript typed-hash equality, an isolated notebook ZIP import and offline reader guards. All are synthetic; no native export, native source read or Fabric SQL was executed. The full-content reader retains data in memory: a fresh native graph/peak/runtime plan is mandatory before its first actual use. Existing local caps do not authorize native history export, whole-mart reads, driver downloads, grants or cloud actions. Full native compatibility, costs and performance remain OPEN; deferred review M1 generic error diagnostics are unchanged.
