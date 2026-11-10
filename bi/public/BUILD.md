# Tableau Public dashboard: build, publish, reconcile

**Status: published on Tableau Public on 2026-11-10, and the published Reconciliation sheet passed 5 of 5.**
On 2026-11-06 the full
713,467-case workbook, built from a Snowflake snapshot, opened in Tableau Public 2026.2.3 with no error,
and the queries behind all four sheets returned rows. Running the app's own SQL again on the packaged
extracts matched the manifest and a separate DuckDB calculation over the CSV files
([evidence](../../docs/evidence/task11-tableau-public-20261105/desktop-load-20261106.json)). That check read
the app's log, not its screen; the user then opened the workbook and confirmed the data on screen is right.
The hand-build steps below are the fallback and have not been walked through in the app.

The workbook was first published on 2026-11-10 at 19:37:43 UTC
([SponsorAtlas public snapshot](https://public.tableau.com/app/profile/simon.d5578/viz/SponsorAtlas-public/SponsorAtlaspublicsnapshot)).
It is a static snapshot, `20261106T052440Z-aa007296` (`ACTUAL_SNOWFLAKE_PUBLIC_MARTS`), exported from the
Snowflake public marts on 2026-11-06 at 05:24:40 UTC, before any scheduled Airflow run. Its 86 posting rows
are the `LOCAL_ARCHIVE_REPLAY` rows of 2026-10-24, so it holds no data from the scheduled runs. On 2026-11-10
at 19:39:42 UTC `reconcile_public.py --url` fetched the published Reconciliation sheet: `case_count` 713,467,
`certified_rate` 0.977778930 (9 decimals in the CSV export), `wage_p75` 156,383.63, `active_posting_days` 86
and `oews_median` 135,980, all PASS at tolerance 1e-8. The same day the published Who, Pay and When sheet CSVs
had 0 differences from a DuckDB calculation over the snapshot CSV files, apart from display rounding. The 5
Hyper extracts in the downloaded workbook are byte-identical to the local ones; only the TWB was re-saved by
Tableau. A scan of the extracts, the TWB and the sheet CSVs found 0 of the 82,905 private employer names
([evidence](../../docs/evidence/task11-tableau-public-20261105/published-20261110/published.json)). See
[known limits](#known-limits-of-the-published-snapshot).

Tableau Public workbooks and their data are public and downloadable by default. Tableau Public is
not for commercial use. Publish only what you accept being public.

## 1. Export

From the repository root on home-ubuntu:

```sh
.venv/bin/python bi/public/export_snapshot.py --source snowflake   # CUBE_SVC/ANALYST; costs credits
.venv/bin/python bi/public/export_snapshot.py --source duckdb      # development only; zero credits
```

`--source snowflake` needs `SNOWFLAKE_ACCOUNT` and `SPONSORATLAS_CUBE_PRIVATE_KEY_FILE`, and refuses
while `.tools/orchestration/STOP` exists. It writes `.tools/bi-public/<stamp>/`. Before any extract it
checks the marts against the fixed oracle: the LCA and OEWS values and the 86 archived job-posting rows.
Daily loads add job-posting rows, so it records the total posting values in the manifest, and refuses
if the written CSV files differ from them. Its warehouse time is not in the guard's ledger.

Every public CSV carries `snapshot_kind` and `snapshot_id`. A `LOCAL_DUCKDB_DEV` snapshot must never be
published: a sheet built from one shows that kind, and `reconcile_public.py` refuses it. Keep the snapshot
directory on home-ubuntu, which reconciliation reads. **Never copy `employer_names_private.csv`.** Note the
snapshot time, the releases and row counts from `manifest.json`.

## 2. Build the workbook

The builder needs the Tableau Hyper API, which is not in `pyproject.toml` (about 90 MB, and only this step
uses it):

```sh
.venv/bin/python -m pip install --target .tools/hyper-api tableauhyperapi==0.0.26963
.venv/bin/python bi/public/build_workbook.py --snapshot <dir>        # refuses a dev snapshot
.venv/bin/python bi/public/build_workbook.py --snapshot <dir> --dev  # local test build, never publish
```

It writes `<dir>/SponsorAtlas-public.twbx`, and telemetry stays off. Copy only that file to the Mac and open
it in Tableau Public. The dashboard should open with no dialog. The `Reconciliation` sheet should read
`case_count` 713,467, `certified_rate` 0.9777789302098064, `wage_p75` 156383.63, `oews_median` 135980 and
`active_posting_days` 86 for the 2026-11-06 snapshot. The published sheet shows `certified_rate` as
0.9777789302098060 on screen and 0.977778930 in its CSV export, both within 1e-8 (see
[known limits](#known-limits-of-the-published-snapshot)).

Two things made the generated file open. Tableau Public refuses a source that is not an extract (error
`3c242d89`), so each fact is packaged as a Hyper file with one `"Extract"."Extract"` table, connected the way
the app's World Indicators sample is. The app also validates the XML against its internal schema, which
depends on the `document-format-change-manifest`, so a file that passes the public XSD can still fail with
`D2E8DA72` ([earlier failures](../../docs/evidence/task11-tableau-public-20261105/load-failures.json)). See `FORMAT_FEATURES` in the builder.

## 3. Build by hand (fallback)

Estimated at 30 minutes, not yet timed. Copy `lca.csv`, `postings.csv`, `wages.csv` and `benchmark.csv` to
the Mac. Open Tableau Public. Add one data source per CSV with **Data > New Data Source > Text file**. Do not
join or blend facts. Keep `is_certified`, `is_active` and `data_role` as whole numbers, `soc` and
`metro_cbsa` as strings, and `received_month` as a string.

**Who sponsors data roles?** (`lca.csv`)
1. Drag `data_role` to Filters, choose **Discrete**, tick `1`.
2. Create calculated fields: `Filings` = `COUNT([case_id])`; `Certified rate` =
   `FLOAT(SUM([is_certified]))/COUNT([case_id])`. Format the rate as a percentage.
3. Put `employer_id` on Rows. Put `Filings`, `Certified rate` and `MEDIAN(annual_wage)` on Columns.
4. Drag `employer_id` to Filters: **Top > By field > Top 25 by Filings**. Sort descending by `Filings`.
   Top N ranks before the `data_role` filter, so right-click `data_role` in Filters and choose
   **Add to Context**.

**Offered pay vs benchmark** (`benchmark.csv`)
1. Filter `soc_label` to `15-1252 Software Developers`, then **Show Filter** and **Single Value (Dropdown)**.
   Filter `metro_top25` to `1`.
2. Put `metro_title` on Rows, sorted descending by `lca_cases`.
3. Put **Measure Values** on Columns. Keep only MAX of `lca_p50`, `lca_p75`, `lca_p90` and `oews_median`.
   Never average or sum them. Put **Measure Names** on Color and Shape.
4. Add this to the title: "LCA percentiles are distributions of offered annual wages, not OEWS
   percentile ranks."

**When employers file** (`lca.csv`)
1. Filter `fiscal_year` to `2025`.
2. Create `Received month` =
   `IF [received_month] < "2024-09" THEN "2024-08 or earlier" ELSE [received_month] END`.
3. Put `Received month` on Columns and `Filings` on Rows. Add to the title: "FY2024 covers Q4 only, so
   there is no year-over-year comparison."

**Reconciliation** (a separate source that only stacks rows)
1. **Data > New Data Source > Text file**, pick `lca.csv`. On the data source page drag **New Union**,
   then drop `lca.csv`, `postings.csv` and `wages.csv` into it.
2. Create these measures, with these exact names:
   - `case_count` = `COUNTD([case_id])`
   - `certified_rate` = `FLOAT(SUM([is_certified]))/COUNT([case_id])`
   - `wage_p75` = `PERCENTILE([annual_wage],0.75)`
   - `active_posting_days` = `SUM([is_active])`
   - `oews_median` = `MAX(IF [soc]="15-1252" AND [area]="1:99" AND [survey_year]=2025 THEN [annual_median_wage] END)`
3. Name the sheet `Reconciliation`. Put the `snapshot_kind` and `snapshot_id` columns and **Measure Names**
   on Rows, and **Measure Values** on Text. Keep only the five measures. Use the columns from the CSV rows
   and never type them: reconciliation compares them with the manifest.
4. Format each with **Format > Numbers > Custom**: 16 decimals for `certified_rate`, 10 for `wage_p75` and
   `oews_median`, 0 for the counts. Turn off thousands separators on the decimals if they get in the way.

**Dashboard.** Add the three views and `Reconciliation`, then a Text object titled "Freshness and
reconciliation" with the snapshot time, releases covered, row counts and `snapshot_id`. Add the line
`Static snapshot from Snowflake; no automatic refresh` only when `snapshot_kind` reads
`ACTUAL_SNOWFLAKE_PUBLIC_MARTS`. Add both disclaimer lines:

> LCA applications are not approvals and do not guarantee sponsorship.
>
> **LCA 申请不等于获批，也不代表这个岗位一定提供担保。**

## 4. Publish

The workbook was published on 2026-11-10 from the installed Tableau Public 2026.2.3 (20262.26.0912.1023),
despite its `AUTOUPDATE: This product version is no longer supported` warning. The app's log shows the
upload at 19:37:35-41 UTC, 2 s before the first-publish time, 19:37:43 UTC. The steps below are how it is done.

1. **File > Save to Tableau Public...** (or **Save to Tableau Public As...**). That is where the 2026.2.3
   Tableau Public app has it; Tableau's help gives Server > Tableau Public, which this app does not use.
2. Sign in with your Tableau Public account.
3. Set the title and click **Save**. The published workbook's title is `SponsorAtlas-public`, the packaged
   file name.
4. **Show Sheets as Tabs** is optional. It is off on the published workbook, and each sheet still has its
   own URL: `reconcile_public.py` reads `views/SponsorAtlas-public/Reconciliation`.

## 5. Reconcile

On home-ubuntu, which holds the snapshot directory, with no warehouse:

```sh
.venv/bin/python bi/public/reconcile_public.py --manifest <dir>/manifest.json \
  --url "https://public.tableau.com/views/<workbook>/Reconciliation"
.venv/bin/python bi/public/reconcile_public.py --manifest <dir>/manifest.json \
  --csv exported-crosstab.csv     # manual export instead
```

`--manifest` is required. It must be an `ACTUAL_SNOWFLAKE_PUBLIC_MARTS` manifest from
`export_snapshot.py`, the public CSV files next to it must match its digests, and the sheet's
`snapshot_kind` and `snapshot_id` must match it. `active_posting_days` is compared with the value in the
manifest and the other four metrics with `projection.json`. It keeps the original CSV and PNG, prints
PASS or FAIL per metric at tolerance 1e-8, and writes `result.json`.

## Known limits of the published snapshot

Checked on 2026-11-10 against the published workbook and the snapshot CSV files.

- The **Who sponsors data roles?** CSV export repeats its 25 rows once per measure pane, 75 rows in all.
  The sheet itself shows 25 rows.
- The **When employers file** note says "FY2025 filings by received month", but FY2025 is the DOL release
  fiscal year. 28,386 of its 594,821 cases were received before 2024-10, the earliest in 2019-10. They show
  as `2024-08 or earlier` (19,205) and `2024-09` (9,181).
- In 26 of the 263 occupations, the 25th metro has the same LCA case count as the first metro left out. The
  tie is broken by metro code, so the top 25 leaves out a metro with equal cases. The default `15-1252`
  view is not affected.
- 14 of the 263 occupations show as `<code> Unknown occupation`.
- 478 of the 4,626 benchmark rows have no OEWS median (411 `NOT_PUBLISHED`, 67 `SUPPRESSED_OR_TOP_CODED`),
  so some metros show no OEWS marker.
- On screen the Reconciliation sheet shows `certified_rate` as 0.9777789302098060; the value is
  0.9777789302098064. The CSV export shows 0.977778930. Both are within the 1e-8 tolerance, but neither
  shows all 16 decimals.
- Workbook download is on, so anyone can download the full 713,467-row case table. Employers are shown by
  ID, not name, but `case_id` is the raw DOL case number, so employer IDs can likely be mapped back to names
  through DOL's public disclosure file.

## Showing employer names (optional, your decision)

`bi/dashboard-spec.md` forbids source names in public projections, so the default is pseudonymous ids.
To use names, review them first; this runs offline and refuses without the review file:

```sh
.venv/bin/python bi/public/export_snapshot.py --names most-filed --snapshot <dir> --write-review-template <private.json>
# Fill in reviewed_by and reviewed_at; review the top 25 and each person-like entry.
.venv/bin/python bi/public/export_snapshot.py --names most-filed --snapshot <dir> --review <private.json>
```

That writes `lca_most_filed.csv` with `employer_label`: employers with at least 50 cases, everyone else
`Other`. Then run `build_workbook.py --snapshot <dir> --names most-filed`, which shows `employer_label` and
labels it `most-filed DOL name; grouping unvalidated`. A hand-built workbook uses that file instead of
`lca.csv`, with the same label.
