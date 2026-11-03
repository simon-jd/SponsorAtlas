# Task13 — Next.js RSC site local evidence

The implementation is in `web/apps/site`; its README contains the runtime contract and reproduction command, `bash web/scripts/site-test-local.sh` from the repository root. This is a local production-mode verification, not a deployment or native Figma/BI acceptance.

## Data and locale contract

Async RSC pages query the true JWT-authenticated Cube REST API with bounded, server-owned LCA queries and finite fetch revalidation. No browser Cube token, secret, SQL endpoint or arbitrary proxy is added. The public employer key is a stable entity-resolution identifier. Search is explicitly identifier-based, excludes UNKNOWN individual identities, and retains UNKNOWN as an aggregate quality count. Details and comparisons preserve fiscal period, identity-history source codes and NULL wages/history, disclose Q4 coverage and salary conversion rules, and state that LCA applications are not approvals or sponsorship guarantees. No source company names, fake trends, posting history or averaged OEWS benchmarks appear.

Next16.4.3 and next-intl4.14.9 are pinned. Actual next-intl routing/request configuration/messages support en and zh. Intl golden outputs cover en-US/zh-CN, USD in both, NULLs and date-only UTC formatting under a Honolulu process timezone. Language navigation preserves pathname/query; theme preference is rendered from a cookie; comparison is bounded URL state. The UI consumes the approved `ui` component APIs and semantic tokens. Render/comparator functions stay inside client adapters.

## Recorded verification

Artifacts are under `web/apps/site/evidence/`:

- `site-red-unit.log`: missing modules before implementation, 0/2 files passing. `site-unit-final.log`: 7/7 tests pass, including locale snapshots, malformed inputs/payloads, NULL history and unresolved aggregate. Additional focused RED evidence documents the unresolved/NULL refinements.
- `site-red-browser.log`: real production skeleton fails axe's serious document-title rule before site implementation. `site-red-localized-title.log`: English title observed on zh before localized metadata was added.
- `site-build-final.log`: successful optimized production build and TypeScript; all three locale routes are dynamic server-rendered routes, not static exports.
- `site-fixture-final.log`: controlled synthetic REST integration exercises both-locale keyboard journeys, page axe, theme persistence, narrow viewports, null/uncertain/period states, production cache expiry and server failure. Fixture pages show an unmistakable synthetic-data banner.
- `site-actual-final.log`: true Cube1.8.0 REST over existing verified public Parquet, with both-locale journeys, all-page axe checks and independent data assertions. `actual-reference.json` records a separate256MB DuckDB2 SQL oracle over public `lca.parquet`, not the protected facts DB. The leading identifier has6491 observed cases: FY2025=2858 with USD170000 median, FY2024=3633 with USD166800 median; history remains PRE_SNAPSHOT_HISTORY_UNKNOWN. Unresolved aggregate=0 in this snapshot.
- `fixture-cache-auth.json`: production POST data cache hit (first query1 backend call, immediate repeat0) and revalidation after2seconds (1 new call); missing fixture JWT401; unavailable backend displays localized error and no substitute rows. Production default revalidation is60seconds. Stable ten-minute JWT buckets avoid per-request fragmentation; cache credentials stay server-only and cached auth artifacts are removed after verification.
- `actual-auth.json`: unsigned real Cube meta request rejected403. `*-browser-budget.json`: initial JS137961gzip bytes, below the200000byte bound; zero browser requests outside the site origin and no server credential/signing identifiers in loaded chunks.
- `*-resources.json`: reviewed process monitor,180s/6GB per phase, owned process cleanup and Chrome profiles. `*-source-protection.json`: protected facts database stat-only before/after; no open/hash/copy. `lifecycle-final.json`: final cache/profile/listener cleanup and peak summary.

Screenshots in `screenshots/` are genuine isolated local browser captures, not generated images. `actual-{en,zh}-{search,detail,compare,dark-narrow}.png` use existing actual public Parquet; `fixture-*` use explicit synthetic data. Dark/narrow captures include keyboard table-region focus and horizontal scrolling. They are supplied for controller visual review.

## Limits and independent gates

The metadata estimate resolves64 incremental package versions; installation selects Linux/x64/glibc only and reuses system Chrome, React, Playwright and axe. The controller approved a1.20GB incremental peak including store/modules, caches, build/test artifacts, Git and the secure SWC temporary directory. No cloud resource, GPU work, new browser download, native-account retry, publish, push or external message occurred.

Axe checks use WCAG2A/AA,2.1AA and2.2AA tags with no exclusions and assert **zero violations**. Keyboard tests use Tab/type/Enter and ArrowRight, without scripted focus/click. These establish the tested local states, not full manual WCAG certification or field p75 LCP/INP/CLS. Local JS measurement is not RUM. Real map/chart/performance work, trial Snowflake operation, cloud deployment and original native Figma/BI acceptance remain separate gates. Cube's inherited no-Cube-Store warning is retained. A cold webpack build can show next-intl's dynamic-import dependency warning; no third-party warning or safety check was suppressed.
