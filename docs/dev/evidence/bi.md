# Task 11 BI evidence — 2026-10-24

Update 2026-11-10: this file covers `bi/tableau` and `bi/powerbi`. Neither has been opened in
its native tool, and their reconciliation is still 5/5 FAIL. The separate `bi/public` workbook,
a static snapshot exported from the Snowflake public marts on 2026-11-06 05:24:40 UTC
(`20261106T052440Z-aa007296`), was published by the user on Tableau Public on 2026-11-10 at
19:37:43 UTC:
<https://public.tableau.com/app/profile/simon.d5578/viz/SponsorAtlas-public/SponsorAtlaspublicsnapshot>.
`reconcile_public.py` read its published Reconciliation sheet at 19:39:42 UTC: 5 of 5 PASS.
It is not connected to Snowflake, does not refresh, and holds no data from the scheduled
Airflow runs. Workbook download is on, so anyone can download the full 713,467-row case table.
See [`bi/public/BUILD.md`](../../../bi/public/BUILD.md).

**Original acceptance: OPEN / blocked.** Actual Power BI report/PBIX/DAX and
Tableau worksheet/VizQL/render have not executed. This workbook was not published
on Tableau Public. A genuine extract-backed TWBX and editable Power BI project
sources are delivered as implementation material, with their limits below.

## Five-number reconciliation

Source manifest SHA256:
`b76f529c69a84b0d9a4305f12ab5825e2675afc636f047006fbe601693801a36`.
Exact source paths/queries/row counts/checksums are in the existing immutable
`.tools/semantic/actual/export-evidence.json`. Query scopes are versioned in
[`bi/queries.json`](../../../bi/queries.json); SQL is [`reconcile.sql`](../../../bi/reconcile.sql).

| Number | Filter | Fresh Cube REST | Independent DuckDB 2 SQL | Actual Hyper SQL (additional check) | Power BI DAX | Tableau VizQL |
|---|---|---:|---:|---:|---|---|
| Latest cases | All archived rows | 238891 | 238891 | 238891 | OPEN | OPEN |
| Certified rate | All archived rows | 0.9777848474827432 | 0.9777848474827432 | 0.9777848474827432 | OPEN | OPEN |
| Annual wage p75 USD | All archived rows, ignore NULL wages | 160000 | 160000 | 160000 | OPEN | OPEN |
| Active posting-days | All original observations | 86 | 86 | 86 | OPEN | OPEN |
| OEWS annual median USD | SOC 15-1252, published area 1:99, survey 2025 | 135980 | 135980 | 135980 | OPEN | OPEN |

Cube execution was new authenticated real Cube 1.8 REST, not copied prior oracle
values. [`cube-rest.json`](../../../bi/evidence/cube-rest.json) includes requests,
responses, timestamp and manifest/query checksums. Tokens were never persisted.
[`reconciliation.json`](../../../bi/evidence/reconciliation.json) records each native
value as missing with FAIL; `cube_sql_match=true`, `native_acceptance=false`.
Hyper SQL runs against genuine files created by Tableau's engine; it is **not**
the Tableau calculation compiler/visual engine. [`hyper-extract.json`](../../../bi/evidence/hyper-extract.json)
preserves that execution origin, SQL and results.

The selected all-data p75 and selected software-developer OEWS median have
different scopes and must not be divided as a meaningful wage comparison. The
interactive comparison source requires explicit matching SOC/geography.

## Artifacts and verified limits

- [`powerbi/sponsoratlas.pbip`](../../../bi/powerbi/sponsoratlas.pbip): three-page,
  30-visual PBIR report source linked to TMDL star model. Actual Snowflake M
  connector and explicit post-trial Parquet branch; exact DAX definitions and
  [`reconcile.dax`](../../../bi/powerbi/reconcile.dax). Microsoft schemas validate
  40 project/report JSON/PBIR files ([result](../../../bi/evidence/pbir-schema.json)). TMDL
  parsing, M refresh, DAX execution and native rendering have **not** been tested.
  `sponsoratlas.pbix` is intentionally absent, not disguised text.
- [`tableau/sponsoratlas.twbx`](../../../bi/tableau/sponsoratlas.twbx): genuine package
  containing source-authored TWB, source manifest and three real Hyper extracts.
  [`sponsoratlas.twb`](../../../bi/tableau/sponsoratlas.twb) has 16 worksheets, an
  overview dashboard plus a matched wage comparison dashboard, shared comparison
  SOC/area/FY/survey-year parameters, scope/status calculations, overview per-sheet
  filter cards and four selection/reset dashboard actions for employer/SOC/metro/FY. Targets
  exclude the fixed benchmark, preventing time/grain leakage. Actual Tableau
  opening/layout, native formulas and interactive behavior remain OPEN.
  [Source validation](../../../bi/evidence/tableau-source-validation.json) checks the
  official 2022.4 XSD, filter/action references, comparison placement and shared
  parameter/row-scope dependencies without independent comparison filters. It retains two upstream-schema
  warnings (no claimed native source-build; unused Explain Data absent). No actual
  application compatibility is claimed from these structural checks.
- Packaged extract row counts are **238891 LCA / 86 postings / 237514 wages**.
  Reopened with the real Hyper API; every field in all **476491** rows matches
  public Parquet under ordered canonical row hashes; verification did not mutate
  extracts ([full-row evidence](../../../bi/evidence/extract-verification.json)).
  Hyper format 3 retains original DECIMAL(24,2), requiring Desktop 2022.4.1+.
- [`dashboard-spec.md`](../../../bi/dashboard-spec.md) specifies grains, metric/null
  behavior, conformed dimensions, filters, drilling, mapping/coverage limitations,
  lifecycle, native completion steps and the required bilingual disclaimer.

## Actual browser attempt

CUA opened the normal Power BI home and observed New report / My Workspace.
Clicking the visible New report entry was rejected by automatic approval. The
stated reason was that the transcript contained the previous read-only rejection
and no subsequent recognized user approval for retry. The action did not execute.
No alternate route or hidden API was tried; this does not establish a web feature
limitation or justify a Windows VM. Native export and tenant identity/capabilities
remain unverified. The tab was marked for user handoff (1645507383).

Actual Power BI home before authoring; no SponsorAtlas report created

This is an environment/pre-authoring screenshot, **not a rendered dashboard**.
No dashboard screenshot is claimed. Parent is handling the precise approval
request; no additional cloud resources or public publication were attempted.

## RED / GREEN and reproducibility

The first file written was `bi/reconcile.sql`. Before implementation it executed
against public Parquet with empty Cube/native evidence tables. It produced all
five expected SQL values and five FAIL rows, ending exit 1 with
`RED: dashboard native engines/artifacts and Cube evidence absent`.

Commands from project root:

```sh
.tools/node-runtime/node-v24.22.1-linux-x64/bin/node bi/capture_cube.cjs
.venv/bin/python bi/reconcile.py --oracle-only
.venv/bin/python -m unittest discover -s bi/tests -v
.venv/bin/python bi/verify_pbir.py
PYTHONPATH=bi/.work/hyper-sdk .venv/bin/python bi/build_tableau.py
PYTHONPATH=bi/.work/hyper-sdk .venv/bin/python bi/verify_extracts.py
.venv/bin/python bi/verify_tableau_source.py
.venv/bin/python semantic/scripts/check_protection.py
.venv/bin/python bi/reconcile.py
```

Initial local GREEN: fresh Cube/independent SQL five values match; eight tests
passed; 40 project/PBIR files validated; real extracts built/reopened/full-row compared.
Round1 source fix ran nine directly covering comparison/Tableau tests, all PASS;
40 PBIR schema checks passed again; TWBX extract hashes remain unchanged. Full
Cube/Hyper/full-row suites were not repeated for source-only edits.
Tests exercise broken Tableau action/card/filter references, amended latest-case selection, certification denominator,
NULL continuous percentiles, repeated observations vs vacancies, zero rows,
multiple/suppressed estimates, missing engines and differing native values.
Fixture SQL equality tests are clearly synthetic and never written as native
actual evidence. Full default reconciliation remains **RED/exit 1** until actual
DAX and Tableau outputs arrive. Native export provenance format and reproduction
steps are in [`bi/README.md`](../../../bi/README.md).


## Review round1: matched comparison source fixes

I1 stays **OPEN/BLOCKED**: no PBIX, DAX/VizQL exports, native dashboard screenshot
or application-open proof. No approval reply or browser retry; Public publication
remains prohibited. I2/I3 are fixed at source level, pending independent review
and actual native operation.

Power BI's PublishedArea selector now maps the selected national/CBSA geography
explicitly into all three comparison percentiles, with single-direction star
relationships preserved. The OEWS measure uses the same selected SOC/area and
independent survey year. Scope/status text exposes invalid/missing/suppressed
benchmarks. Tableau's second dashboard places four matched values and two status
sheets together, all controlled by four shared parameters. No comparison worksheet
has its own divergent SOC/area filter or fixed Software developer title.

[Bounded SQL scope evidence](../../../bi/evidence/comparison-scope.json) is explicitly
**not DAX/VizQL**. SOC15-1252 / all archived FY / survey2025 yields national p75
171440 and median135980; CBSA35620 yields 3319 LCA rows, p75 180000, median166830,
ratio1.0789426362165078. FY2025 reduces that metro to420 rows/p75 179986.5 while
the survey2025 median stays166830. SOC15-1211 gives its own matching numbers;
state2:06 is unsupported and CBSA00000 has no matches. Fixture tests additionally
cover unknown, suppressed and multiple-estimate behavior. Actual DAX follow-up
is prepared in [comparison-checks.dax](../../../bi/powerbi/comparison-checks.dax).

## Resource and input protection

No cloud/SaaS charge, GPU, public publication, extra project, root dependency or
credential change. SDK is an isolated official Apache-2.0 Linux wheel, SHA checked;
90,909,073B download was deleted after 304,810,419B unpacking. It reuses project
Python3.12/cffi2.1.1. [Dependency record](../../../bi/evidence/hyper-dependency.json).
Hyper telemetry off; memory limit 1G; two-CPU affinity; chunked inserts; <150MB
temporary extract cap; capped logs; closed connections/processes. Cube/Hyper ran
sequentially. Observed max RSS: Cube 705532KiB, Hyper 287300KiB, extracting Python
154580KiB. Entire `bi` including ignored SDK/cache is about 312MB before Git-object
overhead, within the approved <1GB added peak and 18e9 project ceiling.

Protected actual DB remains size 1,722,822,656B, mtime1792861877130541034ns, SHA256
`c5c5b2ed0544146f2735682c5ab388930f5e7221c01a910df96561d1a4785e7a`.
It was not opened for mutation or copied. Reviewed Parquet manifests/files remain
unchanged. Existing source-quality/full-FY/ER/human-label/Snowflake/remote gates
remain OPEN; this task cannot close them.
