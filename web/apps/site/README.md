# SponsorAtlas site

A dynamic Next16 App Router site for public LCA evidence. `/en` and `/zh` provide identifier search, `/[locale]/employers/[id]` details and `/[locale]/compare?ids=…` comparison of up to three identifiers. Company names are absent from the public projection; the UI never invents them. The page retains FY/Q4 coverage, identity-history codes, missing wages and the unresolved UNKNOWN aggregate. Different occupation/geography mixes are disclosed; no OEWS averaging or artificial trends are provided.

## Run and verify

Use the existing project Node24, pnpm12 and system Chrome. From the repository root:

```bash
source web/scripts/env.sh
cd web
pnpm install --frozen-lockfile --ignore-scripts
cd ..
bash web/scripts/site-test-local.sh
```

The full gate builds once, uses an explicit synthetic REST fixture for controlled cache/failure assertions, then runs against true Cube1.8 REST on the **existing** verified public Parquet files. It does not prepare/export or open the protected facts database. An independent256MB Python DuckDB query over public LCA Parquet supplies actual expected values. Both browser phases launch production `next start` and ephemeral loopback services, and stop them. Ports3100/4100 must be unused; occupied ports are rejected. All processes run under the reviewed180second/6GB monitor. No cloud service or personal browser profile is used.

For a separately configured runtime, use `.env.example` as a list of **server environment** settings. Supply them to the process (never `NEXT_PUBLIC_*`); `next start` does not inherit Cube's environment automatically. Run the existing Cube service with explicit Parquet or Snowflake source selection. Snowflake remains subject to its existing trial/key-pair guards. The site declares its configured source and fails visibly when unavailable. `SITE_DATA_SOURCE=fixture` is only for explicit tests and always shows a synthetic-data banner.

The focused cache-identity regression uses a synthetic REST backend with the same endpoint, secret, production build, data-cache directory and actual signing bucket across restarts. It checks source-only and snapshot-only transitions plus missing/malformed Parquet identities. Run it after the build, from `web/apps/site` (start with at least 90 seconds remaining in a ten-minute clock bucket):

```bash
SITE_FIX_PHASE=regression python3 ../../scripts/run-monitored.py site-cache-transition bash ../../scripts/site-command.sh node ../../scripts/site-cache-transition-run.mjs
```

`tests/e2e/focus.spec.ts` is part of the ordinary browser gate and checks actual focused-card pixels and contrast in both themes after real Tab navigation. Fix-round captures and logs use `fix1-*`/`site-fix1-*` names; they retain the original failing output separately. These regression fixtures are not actual Parquet/source verification.

## Data and UI boundaries

- Async server pages call `lib/cube.ts`, protected by `server-only`. No API proxy route accepts arbitrary queries. Query builders own every member/filter/order and cap search at13 rows (12 displayed plus overflow notice), details at101 (refuse truncated results beyond100), comparison at3 IDs, and input at80 safe identifier characters.
- Authenticated POST uses `cache: 'force-cache'` with finite `next.revalidate` (default60seconds). A deterministic ten-minute JWT bucket, expiring after30minutes, prevents per-request cache fragmentation; token rotation also creates a new cache key. Validated source and dataset version are included in server request headers, so they form part of the fetch cache key. Parquet requires a lowercase SHA-256 in `SITE_SNAPSHOT_SHA`; fixture/Snowflake require an explicit `SITE_SOURCE_VERSION` (letters/digits/dot/underscore/colon/hyphen, at most100characters). Update the version whenever replacing that dataset. Displayed provenance uses the same validation and never claims a missing/malformed Parquet digest. Cache can serve a stale value while revalidation completes **within the same source/version**. Source snapshot refresh is separate. Never publish `.next/cache`: it contains server fetch metadata, including authorization headers.
- `next-intl` owns locale routing, request messages and translation. `formatCurrency`, `formatNumber`, `formatDate` use Intl with en-US/zh-CN, USD in both, explicit UTC for date-only values and localized missing values. Dates require valid ISO date-only strings.
- `ui` provides Button/DataTable and theme tokens. Table callbacks are defined in the client adapter; RSC boundaries receive serializable rows/labels. Language links preserve path/query (including search and comparison); theme cookies give an initial server theme without a flash. Comparison is URL state and supports shareable links, adding more from search and removing entries.
- Source codes are retained verbatim, with translated explanations. Null history/wages remain unavailable. `UNKNOWN` is displayed only as an aggregate data-quality count. No source company names, coordinates, personal data or raw payloads are exposed.

## Evidence and limitations

`evidence/*-resources.json` records process ownership/cleanup and sampled RSS; `evidence/screenshots/` contains actual local production-browser captures, explicitly separated into `fixture-*` and `actual-*`. Actual means real existing public Parquet, **not a cloud deployment or fresh source acquisition**. `fixture-cache-auth.json` is the controlled production-cache observation; `actual-reference.json` is independent source SQL; actual Cube rejects unsigned meta requests with403. Browser budget evidence measures gzipped initial JS responses (<200000bytes), not p75 field performance.

Normal config/cache/tmp is project-local. SWC's ancestor-permission validation requires the permitted exceptional atomically-created0700 `/tmp/sponsoratlas-task13-swc.*` directory; the wrapper records its exact path/size and removes it. The dependency estimate includes all optional platforms but installs only Linux/x64/glibc. See `resource-plan.json` for controller-approved limits.

Axe WCAG2/2.1/2.2 checks plus pure-keyboard journeys are automated evidence, not a complete manual WCAG2.2AA certification. Field LCP/INP/CLS, real map/chart engines, native Figma acceptance, BI acceptance and cloud deployment remain separate gates. The inherited Cube Store warning is visible; no Cube Store is used. Next-intl's webpack dynamic-import cache-dependency warning may appear on a cold build; the production gate verifies the resulting output, not future cache invalidation behavior.
