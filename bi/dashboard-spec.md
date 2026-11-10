# SponsorAtlas BI specification and implementation status

The original acceptance is **OPEN**. The repository contains editable Power BI
PBIP/PBIR/TMDL and a Tableau TWB/TWBX with genuine Hyper extracts. No PBIX has been
created, and neither report has been opened/rendered in its native application.
Structural checks and Hyper SQL do not establish DAX or Tableau VizQL execution.
For the public dashboard `bi/public` supersedes `bi/tableau` and `bi/powerbi`
(see [native acceptance](#source-lifecycle-and-native-acceptance)).

## Sources and grains

Use only `semantic/projections.json`, with the exact reviewed public snapshot
manifest `.tools/semantic/actual/export-evidence.json`. Keep its source archive
lineage and SHA256; never overwrite the source snapshots. Source facts are latest
LCA case_number, posting_id × original UTC observation_date, and published OEWS
SOC × AREA_TYPE:AREA × survey_year. The source release behind the BI evidence is
archived Q4 coverage (238,891 cases, [reconciliation](evidence/reconciliation.json)),
not a complete fiscal-year population. Current employer identity/history is
provisional and must not be presented as human-validated historical attributes.

Unknown employer/SOC/metro and unresolved mapping statuses remain available.
Only metropolitan CBSA is called metro; no county expansion or micropolitan
substitution. `1:99` is the published national OEWS area. State and other area
types retain their explicit published identity. Public projections contain no
applications/email/RAW payload/source employer names/coordinates.

Power BI uses unique conformed Employer, SOC, Metro, FiscalYear, ObservationDate,
SurveyYear and PublishedArea dimensions. All relationships are dimension-one to
fact-many, single direction. Employer and FiscalYear filter LCA/postings only;
SurveyYear/PublishedArea relationships filter OEWS only (comparison measures explicitly
map the selected PublishedArea to LCA metro); ObservationDate filters postings only.
No relationships join one fact to another. Tableau uses three independent
extract data sources, one per fact; they never physically join or blend rows.

## Metrics

| Metric | Definition and empty/null behavior | Required five-number scope |
|---|---|---|
| Case Count | Count every latest unique case, including unknown/malformed retained records. Empty = 0. | All archived LCA rows |
| Certified Rate | Certified plus Certified - Withdrawn / every latest case; empty denominator = NULL. Uses source is_certified. | All archived LCA rows |
| Wage P50/P75/P90 | Exact continuous inclusive percentile of non-NULL annual_wage. Empty wages = NULL. Never average percentile aggregates. | P75 over all archived LCA rows |
| Active Posting Days | Sum is_active at posting_id × UTC observation date; not distinct vacancies. | All actual observations |
| OEWS Median | A_MEDIAN only when exactly one estimate remains; otherwise NULL. Suppressed/top-coded numeric values remain NULL, with median_raw/wage_status visible. | SOC 15-1252, area 1:99, survey year 2025 |

Annualization occurs upstream: hourly × 2080, weekly × 52, biweekly × 26,
monthly × 12. Both dashboards consume annual_wage without annualizing again.
Federal fiscal year and OEWS survey year have separate controls; neither implies
the other. Percentiles are distributions of LCA offered annual wages, **not**
OEWS percentile ranks. The available OEWS projection has an annual median, so the
comparison is a dollar comparison or LCA percentile / official median ratio.

Every Power BI comparison percentile and its ratio requires one selected SOC and
a national benchmark or matching metropolitan area. PublishedArea is the common
geography control: `1:99` explicitly clears Metro for national LCA; `4:<CBSA>`
clears residual Metro filters and applies that CBSA to `lca[metro]` with TREATAS.
The comparison OEWS measure clears residual Metro, retaining the exact published
area/SOC/survey year. Single-direction star relationships stay unchanged. State/nonmetropolitan comparison lacks an equivalent public
LCA area mapping and returns blank. The all-data reconciliation p75 and the
software-developer national benchmark are deliberately different query scopes;
their ratio is not a substantive comparison. The fixed overview benchmark card
is labeled with its scope and does not respond to the overview slicers.

## Pages, filters and drill paths

Power BI source has 3 pages / 30 visuals. Overview: coverage strip, five KPI cards,
Employer/SOC/Metro/FiscalYear slicers, cases grouped by employer/SOC/metro, original
observation-date columns and explicit trend-coverage status. WageComparison:
separate SOC/published-area/survey-year/fiscal-year slicers, p50/p75/p90, exact
OEWS scalar, guarded ratio and benchmark-status text. DataQuality: employer
history statuses and raw OEWS wage suppression/status table. Each page includes
the disclaimer below. No smoothed line or synthetic observations are generated.

Tableau source has 16 worksheets and two dashboards. Overview contains the five
reconciliation KPI scopes, employer/SOC/metro/FY counts and original-date postings.
Its four on-select actions filter employer, SOC, metro and fiscal year; clearing
selection resets each action. Targets exclude the source sheet and fixed OEWS
benchmark. Per-worksheet filter cards/slices apply to overview sheets only.

The **matched wage comparison** dashboard contains LCA p50/p75/p90, exact OEWS
annual median and two dynamic scope/status sheets. Four workbook-wide parameter
controls set shared SOC, shared published area, LCA fiscal year (0 = all archived)
and OEWS survey year. Defaults are 15-1252, 1:99, 0 and 2025. These are editable
value controls; enter another detailed SOC or `4:35620` to select metropolitan
CBSA 35620. All four values derive from the same SOC/area parameters, with explicit
national-or-CBSA mapping inside their row conditions. No independent worksheet
filters can leave one comparison value at a different selection. FY constrains
only LCA; survey year constrains only OEWS. Scope text reflects selection and
missing/suppressed/multiple/unsupported states. Unsupported state/nonmetropolitan
areas return empty comparison numbers; unmatched areas return no-data states.
There is no physical fact join, reverse relationship or average of official medians.
These dollar values constitute the comparison; the p75/median ratio is additionally
shown in Power BI. Native parameter interaction, formatting and keyboard behavior
remain unverified.

Drill workflow: select one employer/SOC/metro, inspect the status/coverage table,
then compare periods within the same fact grain. Do not drill to private records.
Employer → SOC → metro is a sequence of independent selection actions, not a claim
of a geographic/employer hierarchy. Tableau's four dashboard actions explicitly
exclude the fixed OEWS benchmark; never propagate an LCA FY filter onto OEWS.
Clear selections and reset worksheet filter cards before reconciliation exports.

Native completion must verify WCAG 2.2 AA: meaningful titles/alt text, keyboard
order, visible focus, no color-only encodings, 4.5:1 normal-text contrast, readable
empty/suppressed states, and data-table alternatives. Current PBIR tab order is
explicit; visual accessibility has not been measured. Browser LCP/INP/CLS and JS
budgets belong to the frontend and are not claimed from these source files.

## Required visible disclaimer

LCA applications are not approvals and do not guarantee sponsorship.

**LCA 申请不等于获批，也不代表这个岗位一定提供担保。**

## Source lifecycle and native acceptance

Power BI `Public_lca`, `Public_postings`, `Public_wages` M expressions implement
the actual Snowflake connector (`Implementation="2.0"`), fixed project
ANALYTICS.MARTS projections/ANALYST role/SPONSORATLAS_XS, bounded query timeout and
explicit active <=30-day trial window with seven-minute cutoff. Credentials are
entered only via the application's KeyPair credential flow. No passwords or keys
are embedded. Before using it, run the existing semantic remote preflight to
verify live identity, unique keys and XS/auto_suspend=60. That preflight passed for
Cube on 2026-10-31
([verify-snowflake.json](../docs/evidence/task07-cube-snowflake-20261031/verify-snowflake.json));
these M expressions have not run against Snowflake. Do not grant CUBE_SVC new permissions.

The source default is **Parquet**, explicitly selected. Set SnapshotDirectory to
the verified local snapshot. The model calls `Parquet.Document(File.Contents())`
directly, and the reconciliation oracle uses the installed DuckDB. Before trial
expiry export and verify a new snapshot using the existing semantic lifecycle;
stop Snowflake refresh and choose Parquet explicitly. Import refresh must never
silently fall back to another source. Desktop model import/cached data remains
distinct from a billable remote refresh.

Open `powerbi/sponsoratlas.pbip` (or the Report/definition.pbir) in Power BI Desktop,
set the source parameters, refresh, resolve any native parse/runtime findings,
inspect all visuals, run `powerbi/reconcile.dax` in DAX Query View, export the five
rows, and Save As `powerbi/sponsoratlas.pbix`. A source project is not a PBIX.

Open `tableau/sponsoratlas.twbx` in Tableau Desktop/Public Desktop 2022.4.1 or newer
(Hyper file format 3 retains DECIMAL(24,2)). Validate/repair the source-authored
workbook in the app, verify the serialized filter cards/actions, execute each
of the five KPI worksheets, export exact values, take native screenshots and
save the verified local TWBX. Hyper SQL tests do not execute Tableau PERCENTILE
or the worksheet's calculated fields. `tableau/sponsoratlas.twbx` has not been published. The public
dashboard is the separate `bi/public` workbook, a static snapshot published on Tableau Public on
2026-11-10 (see [`public/BUILD.md`](public/BUILD.md)).

On 2026-10-24 CUA reached the normal Power BI home. Automatic approval rejected
the visible New report action because the earlier read-only rejection was not
followed by recognized user approval. No report was created; no alternate URL
or hidden API was tried. This is an approval gate, **not** evidence that web
authoring is unavailable. Snowflake key-pair access works for Cube
([run.log](../docs/evidence/task07-cube-snowflake-20261031/run.log)) but has not been
used from a BI tool. Tenant/native export capability, PBIX/DAX/render and the native render of `bi/tableau`
remain unverified. The separate `bi/public` workbook is published on Tableau Public (see
[`public/BUILD.md`](public/BUILD.md)).

References: [Power BI PBIR](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report),
[TMDL](https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview),
[Snowflake connector](https://learn.microsoft.com/en-us/power-query/connectors/snowflake),
[PBIX download limits](https://learn.microsoft.com/en-us/power-bi/create-reports/service-export-to-pbix),
[Tableau aggregate calculations](https://help.tableau.com/current/pro/desktop/en-us/calculations_calculatedfields_aggregate_create.htm),
[packaged workbooks](https://help.tableau.com/current/pro/desktop/en-us/save_savework_packagedworkbooks.htm),
[Hyper format compatibility](https://tableau.github.io/hyper-db/docs/hyper-api/hyper_process/).
