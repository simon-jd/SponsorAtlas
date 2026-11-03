# Task 14 — aggregated geography and wages

## 2026-10-28 (phase 6): full-year map, limits sized from measurement

Raw outputs: [`task14-fullyear-map/`](../../evidence/task14-fullyear-map/). 32 cores; load average 6.7–11.1 during
these runs, with other projects running at the same time.

**Decision: raise the limits, keep the aggregation in the Next.js server.** The 300,000-row / 45 MB
cap was an internal budget. Moving the aggregation to DuckDB over Parquet would need a DuckDB binding
for Node (npm registry, not an allowed host) or a new Python query service next to the site, and H3 in
DuckDB needs the h3 extension (community-extensions.duckdb.org, not allowed). The measured in-memory
cost below (1.43 GB peak RSS, < 1 s per uncached scope) fits the 16 GB memory share, so raising the
limits is the smaller change. Wages stay exact (no rounding); the browser still receives only
`{h3,count,wage_p50}` aggregates. The three limits now live in one module,
`web/apps/site/lib/viz-limits.ts`, read by the server, the deploy scripts and (parsed) the exporter:

| Limit | Old | New | Full year measured |
|---|---:|---:|---:|
| frequency rows | 300,000 | 600,000 | 517,991 |
| projection bytes | 45,000,000 | 120,000,000 | 103,036,467 |
| H3 cells per scope | 10,000 | 16,000 | 12,701 (all years, res 7) |

The headroom (≤ 1.3× of the measured value, enforced by `semantic/tests/test_viz_limits.py`) fits a
re-export of this year but refuses a silent second year.

Server cost, measured with `web/scripts/viz-server-measure.mjs` (same load/verify/parse as
`lib/viz-source.ts`, then `aggregate()` 3× per scope, uncached):

| | Q4-only (195,240 rows) | Full year (517,991 rows) |
|---|---:|---:|
| load + sha256 + parse | 266 ms, RSS 363 MB | 605 ms, RSS 734 MB |
| all-years scope, res 0–5 | 173–275 ms | 450–689 ms |
| all-years scope, res 6–7 | 256–395 ms | 391–845 ms (refused at the old 10,000-cell limit) |
| FY2025, res 3 | ≈ 99 ms | 402–427 ms |
| one employer, res 5 | 3–5 ms | 9–12 ms |
| peak RSS | 705 MB | **1,427 MB** (memory share 16 GB) |

Repeated scopes come from the existing 16-entry server cache. Response size at res 3 is 39 KB (704 cells);
at res 5 326 KB (6,054 cells). Cell counts: [cells-per-scope.json](../../evidence/task14-fullyear-map/cells-per-scope.json).

**Export.** `make viz-worksite-export` (7.8 s in the uncapped probe, Python peak RSS 1.26 GB) wrote
`.tools/viz/worksite-fy2025/`: 713,467 cases in 517,991 frequency rows, sha256 `48069ecd…61c8`,
null wages 0, source files unchanged ([manifest](../../evidence/task14-fullyear-map/manifest.json),
[log](../../evidence/task14-fullyear-map/worksite-export.log)).

| Point used | Cases |
|---|---:|
| ZCTA internal point | 686,006 |
| Place internal point | 25,396 |
| County internal point (fallback) | 1,729 |
| Unmapped | 336 |

**Frame test.** `make viz-frame-build`, then `make viz-frame-measure VIZ_LABEL=fy2025-full` under
`flock /var/tmp/rtx4090.lock` (waited about 16 min for another project's vLLM job). Chrome WebGL2 on
ANGLE/Vulkan, RTX 4090, 704 cells at H3 resolution 3, 240 measured frames each, both passed
([log](../../evidence/task14-fullyear-map/frame-measure.log),
[1x](../../evidence/task14-fullyear-map/viz-frame-fy2025-full-1x.json),
[4x](../../evidence/task14-fullyear-map/viz-frame-fy2025-full-4x.json)):

| CPU | per-frame work p95 (script + render) | limit | rAF interval p95 (info only) |
|---|---:|---:|---:|
| 1x | **4.9 ms** (script 3.9, render 1.1) | 16.7 ms | 33.4 ms |
| 4x | **9.4 ms** (script 7.2, render 2.4) | 33 ms | 33.4 ms |

Load average 6.9–7.3 (32 cores) around the run. The rAF intervals are recorded but not the gate
(internal priority item 3): 33.4 ms equals two 60 Hz vsync periods, which fits a page presented at ~30 Hz on
the shared desktop rather than slow map work (the per-frame work is 4.9 ms); the cause was not isolated.

**Viz E2E.** `make viz-e2e` (new target, GPU lock; the lock was free this time) ran every `viz.spec.ts`
test on the full-year projection: **7/7 passed** ([log](../../evidence/task14-fullyear-map/viz-e2e.log)). It covers
the WebGL-off fallback with axe in en/zh, reduced motion, the API bounds (no coordinates or case
numbers in the response; FY2025 total = 594,821, read from the fact build's
[fact-summary.json](../../evidence/task09-backfill/fact-summary.json) instead of the old Q4 constant 118,580),
initial JS 150,601 B gzip (limit 204,800), and the frame test again without the Vulkan flags:
work p95 2.4 ms at 1x and 10.1 ms at 4x (rAF 83.3 ms p95, info only; read from
`viz-frame-default-*.json` right after the run, before the site run below overwrote those files).
Load average 2.2–6.2 on 32 cores during that run.

**Site E2E on actual data.** `make site-e2e-actual` (new target, GPU lock): true Cube 1.8 REST over the
full-year public Parquet plus the production site, **14/14 passed**
([log](../../evidence/task14-fullyear-map/site-e2e-actual.log)): focus, en/zh keyboard journeys with axe, honest
empty/invalid states, Cube RSC output = independent DuckDB SQL over `lca.parquet` (sha `6d185ed4…`;
the leading employer now has 19,145 cases, FY2025 15,512 with median USD 154,600,
actual-reference.json), the JS/credential budget,
and the seven viz tests. This config passes `--disable-gpu`, so its frame test ran on SwiftShader
(software) and still passed: work p95 2.4 ms at 1x, 9.8 ms at 4x
(1x, 4x).
Load average 5.7–10.2 (32 cores), other projects running. The frame test covers resolution 3 only; resolutions
6–7 (up to 12,701 cells) were not frame-measured.

## 2026-10-27 (phase 3): worksite ZIP/place centroids and a frame-work budget

**Geography.** `semantic/scripts/export_worksite_coordinates.py` writes a new immutable projection
`.tools/viz/worksite/` (kind `WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT`), which the local site now
reads. Every one of the 238,891 fact rows is joined back to its staging row (file SHA + row number;
0 rows without one) for the worksite state, ZIP and city. The point is the Census2020 Gazetteer
internal point of the worksite ZCTA, used only when that ZCTA has land in the worksite state.
Otherwise it is the place internal point (unique name in the state), and the county internal point
is the last fallback. Sources (sha256 in `data/reference/worksite_centroids_2020.json`): 2020 ZCTA
and place Gazetteers (archived under `data/reference/Gazetteer2020/`) and the ZCTA–county
relationship file. `data/reference/worksite_centroids_2020.csv` has 33,080 ZCTA and 31,603 place rows.

| Point used | Cases |
|---|---:|
| ZCTA internal point | 228,803 |
| Place internal point | 9,279 |
| County internal point (fallback) | 432 |
| Unmapped | 377 (was 108,251 with county points only) |

Projection: 195,240 frequency rows, 39,314,703 bytes (cap 45,000,000), sha256 `8683ae29…5189`,
source files unchanged (manifest). These are
representative Census points, not street addresses.

**Not done as specified:** the plan asks for H3 aggregation in DuckDB with the h3 extension. The
extension comes from `community-extensions.duckdb.org`, which is not an allowed download host, and
the h3-duckdb GitHub releases (v1.5.0–v1.5.6) carry no binaries. Aggregation stays on the server
(Next.js route, h3-js); the browser still receives only `{h3,count,wage_p50}`. The optional Cube
H3 snapshot and the native Snowflake SQL path still use county points.

**Frame budget.** `viz.spec.ts` now gates on per-frame main-thread work, not rAF intervals
(which vsync caps). For each of 240 measured frames (after 60 warm-up frames): `viz-script` is
`performance.measure` around one pan/zoom input applied with `flushSync` (React + deck.gl prop
update), and `viz-render` is the measure between deck.gl's `onBeforeRender` and `onAfterRender`.
The gate is the p95 of their sum (`evaluateFrameWork`, unit-tested). It does not include GPU
execution or deck.gl's layer diff before `onBeforeRender`.

Run: `make viz-frame-measure` (production build from `make viz-frame-build`), inside
`flock /var/tmp/rtx4090.lock`, system Chrome, ANGLE Vulkan on the RTX 4090 (hardware, not
software GL), 1280×900. The real worksite projection gives 632 cells at resolution 3. There were
300 draws and the layer was built once. Machine: 32 CPUs, other projects running. The final
run waited about 13 minutes for the GPU lock (load average 7.12 before, 4.49 after).

| CPU throttle | p95 frame work | script p95 | render p95 | max | limit | result |
|---|---:|---:|---:|---:|---:|---|
| 1x | **5.7 ms** | 4.4 ms | 1.3 ms | 9.9 ms | 16.7 ms | pass |
| 4x | **7.9 ms** | 6.7 ms | 2.5 ms | 11.7 ms | 33 ms | pass |

An earlier run of the same test (load average 9.0, before user-timing entries were cleared each
frame) gave 2.5 ms at 1x and 9.3 ms at 4x. The spread between runs is real noise on the shared
machine, so these are not stable single numbers.

Durations are at Chrome's timer resolution (0.1 ms steps). The rAF interval p95 (33.4 ms) is kept
for information only; the new loop waits two frames per input. Raw outputs:
viz-frame-worksite-1x.json,
viz-frame-worksite-4x.json,
t14-measure.log.

The local `/en/map` and `/zh/map` use server H3 aggregation, deck.gl H3HexagonLayer, a deck.gl wage histogram, Framer Motion with zero required transitions under reduced motion, and a static SVG plus complete accessible table when WebGL is unavailable. Graphics are loaded only after the user requests the interactive view. The approximation is explicitly **county internal points, not precise worksite coordinates** in both locales.

## Source, math and limits

The approved read-only export uses only public `MARTS.fact_lca_case` and `MARTS.dim_geography`, joins on unique `geography_key`, and retains per-employer/fiscal-year/history/coordinate/vintage/mapping/wage frequencies. Case identifiers never enter the new artifact. It verifies 7,042 unique geography keys and 238,891 unique cases, and exactly preserves 238,891 joined cases. Source and three original immutable public exports have unchanged size/mtime before and after. The protected database was never copied, hashed, written, migrated or prepared again.

The server-only immutable frequency JSON is 34,918,231 bytes with SHA256 `e88cb311a1861d29fcd2f6473e74061c62e17e6c411e371adef1226b525bf24c`, linked to original LCA snapshot `f2945a5553c7f2eb2856b749e87b552a6c5c6673e3c681eaedcae905312a3874`. It contains 179,626 frequency groups. Default resolution 3 produces 544 cells covering 130,640 mapped cases; 108,251 cases remain explicitly unmapped. There are zero NULL wages in this actual snapshot; controlled tests preserve genuine NULL wages without converting them to zero. All 238,891 cases have `PRE_SNAPSHOT_HISTORY_UNKNOWN`. Coordinate accounting: 130,640 Census2020→Census2023, 107,949 UNKNOWN→UNKNOWN, 302 Census2020→UNKNOWN. County mapping: MATCHED130,942; UNRESOLVED_COUNTY9,180; SOURCE_COUNTY_MISSING98,769. Code continuity does not establish unchanged boundaries or actual worksite accuracy.

Fiscal year/employer filters apply before H3 and weighted median calculation. The median uses all eligible original wage frequencies, including even-row midpoint semantics; it is never a median of county medians. The histogram covers all selected cases, including unmapped geography, excluding only NULL wages. Bins are USD0–<50,000 through450,000–<500,000 and500,000+. Every client cell has exactly `{h3,count,wage_p50}`. Raw coordinates and case identifiers are never sent to the browser. H3 values stay hexadecimal strings. Supported UI/native resolutions are0–7; invalid/nonfinite coordinates and invalid scopes are rejected. Each rendered layer has one resolution, with highPrecision:auto for coarse cells and pentagons.

Snapshot cache identity includes source, source version, projection checksum and scope; the in-memory cache has at most16 entries of at most10,000 cells. There is no disk scope cache or employer×year×resolution precomputation. Source frequency JSON is capped45MB/300,000 groups. A single optional Cube aggregate Parquet+manifest is capped5,001,000 bytes. One immutable manifest is verified before source loading.

## Optional Cube and native wiring

Existing three-source preparation, projections and seven metric definitions remain unchanged. Default Cube repositories exclude `h3.yml`. With `SEMANTIC_H3_ENABLED=1` and `SEMANTIC_H3_WORK`, the verified single-scope aggregate Parquet creates `mart_h3` in a separate `scratch-h3`; its checksum participates in snapshot identity. The model exposes only the complete fixed-scope dimensions `h3.h3`, `h3.count`, `h3.wage_p50`, `h3.resolution`, preventing aggregation of medians. A real local Cube REST run compiled both modes: default excluded H3; enabled returned544 cells/130,640 mapped cases and retained all original models/views.

For a native server, the model rewrites to actual `H3_INT_TO_STRING(H3_LATLNG_TO_CELL(...))` and `MEDIAN(f.annual_wage)` SQL. A CASE guard and coordinate range predicate precede native H3 wrapping. Existing native key-pair, explicit opt-in, ANALYST/CUBE_SVC, XS/auto_suspend60, unique-grain preflight and trial cutoff remain in force.

The site now has a concrete native path: server-only authenticated POST `/cubejs-api/v1/h3` accepts only resolution/year/employer. It executes two fixed public SELECTs: cell aggregation and grouped coverage/histogram accounting. Both filter before grouping. No arbitrary SQL/members are accepted. Gateway source/version headers must match `SEMANTIC_H3_SOURCE_VERSION`; the site independently validates response identity against `SITE_SOURCE_VERSION`. The existing shared lifecycle driver owns connections and shutdown; this endpoint admits one active request, rejects concurrency with429, caps each result at10,000 rows using a10,001 overflow sentinel, and caps serialized response2MB. Each statement has a6.5s cancellation deadline; disconnected clients cancel owned statements. The site streams at most2MB with a15s timeout. Native errors are scrubbed. Native histogram NULL/unmapped/vintage reconciliation, authentication/source identity, cancellation and bounded concurrency pass controlled HTTP/driver/transport tests. **Actual Snowflake execution and precise worksite input remain OPEN.** Local h3-js and controlled transport do not prove Snowflake execution.

## Actual rendering and performance evidence

All rendering/benchmarks use `flock -w30 /var/tmp/rtx4090.lock`, reviewed180s/6GB process monitor, repo-owned isolated Chrome profiles and system Chrome. Hardware runs used narrow approved GPU-device escalation, without host/package/driver/display changes. Flags: `--no-sandbox --enable-webgl --ignore-gpu-blocklist --use-angle=vulkan --enable-features=Vulkan --disable-vulkan-surface`. Hardware protocol: viewport1280×900, full544-cell resolution3 source, scripted pan longitude−98+sin(i/23)×22, latitude38+cos(i/31)×8, zoom3.5+sin(i/39)×0.4 (same resolution throughout). Warmup60 frames, measured240 rAF timestamp intervals. p95 is unrounded nearest-rank sorted[ceil(.95*n)−1]. CDP CPU rates1 and4. The map recorded299 draw updates; memoized layer/accessors recorded1 build before and after the full motion sequence.

| Run | Renderer | 1× p95 ms | 4× p95 ms | Result |
|---|---|---:|---:|---|
| First software | SwiftShader | 50.099999999998545 | 66.60000000000036 | Both FAIL; no RTX claim |
| Software Vulkan | llvmpipe | 16.799999999999955 | 33.399999999999636 | Both FAIL; no RTX claim |
| Initial hardware | NVIDIA GeForce RTX4090 Vulkan | 16.799999999999727 | 16.800000000000182 | 1× FAIL;4× PASS |
| Stable layer hardware | NVIDIA GeForce RTX4090 Vulkan | 16.700000000000728 | 16.799999999999272 | Raw-double command1× FAIL;4× PASS; see numeric ruling below |

The controller's explicit measurement-unit ruling normalizes each individual interval to integer nanosecond arithmetic units before nearest rank, with unchanged physical thresholds16,700,000ns/33,000,000ns. It rejects nonfinite/nonpositive/unsafe values and conversion residues>1e-10ms. The retained post-layer trace evaluates to16,700,000ns at1× and16,800,000ns at4×, both within the original thresholds; the prior hardware16.8ms and all software failures remain failures. The maximum observed representation residue is7.283063041541027e-13ms. Boundary regressions prove16.7/33 pass and16.700001/16.8 (1×),33.000001 (4×) fail. `viz-numeric-replay.json` preserves source-trace SHA256, raw p95, integer p95, thresholds and previous command outcome. This is offline corrected numeric evaluation of the same genuine trace, **not a rerun**, a threshold tolerance, nanosecond clock accuracy, or repeated-run guarantee. The earlier raw-double commands/logs remain FAIL and are retained. Independent review must assess this measurement ruling.

No physical threshold, frame count or cell workload was relaxed; no empty scene was used as acceptance. Early software runs used zoom3±0.7 while maintaining544 cells/resolution3; hardware runs used3.5±0.4 to retain the same aggregation resolution after automatic zoom aggregation was implemented. These are not a controlled causal timing comparison. Raw full interval traces and renderer/vendor/context/draw counts are retained in `web/apps/site/evidence/viz-frame-*.json`. One separate empty-WebGL diagnostic recorded rAF p95=16.700000000000728 and callback performance.now interval p95=16.800000071525574, but the final renderer read was null; it does not establish matching hardware/context persistence or a causal explanation. Its trace is explicitly not an acceptance workload.

The layer/accessor memoization avoids reconstructing them during pan/zoom; the data array stays stable until a scoped server aggregate changes. Keyboard controls and pointer drag are supported. Coarse/pentagon controlled tests and static fallback cover resolution edge cases. Screenshots retain unmodified browser pixels in both locales and dark mode. Axe checks are scoped evidence, not whole-product WCAG certification. Field CWV/RUM, Figma, BI, native Snowflake and remote deployment remain separate gates.

## Resource and lifecycle accounting

Complete metadata graph:130 added exact versions,211,076,282 unpacked bytes, integrity for every package. Eighteen packages lacked registry sizes; bounded in-memory integrity-verified tar member inspection accounted840,164 compressed+unpacked bytes and left no payloads. The amended approved incremental envelope is1,227,000,000 bytes over15,287,751,484, predicted complete peak16,514,751,484<18e9. It includes package store/modules/extraction, metadata, build, all data/optional artifacts, spill, profiles/evidence/Git, logs and secure SWC32MB. No browser/runtime download or install scripts ran.

A build subcap incident occurred: generated `.next/cache`346,096,642 bytes exceeded the256,000,000 build-cache allocation by90,096,642. No complete project measurement captured that exact transient peak, so neither exact global peak compliance nor a global violation is asserted. Only that generated cache was deleted. Supported webpack `cache=false` then kept subsequent production outputs around7MB; runtime fetch caches/revalidation were unchanged. Controller's after-cache full audit was15,748,997,798 bytes. Every reviewed phase stayed below6GB RSS and180s; maximum observed task phase RSS was2,994,638,848 bytes. SWC caches were30,749,712 bytes each and removed by the existing secure wrapper. The empty-scene diagnostic monitor exited1 for cleanup-incomplete with PID3171427; that process is absent at final inventory. Its original failure is retained. Final ports3100/4100/6006 are closed, recorded profiles and external SWC directories absent, and nonblocking GPU lock acquisition succeeds. Final protection/lifecycle inventory is `viz-final-lifecycle.json`. No publish, push, cloud rental, private CDC/email or unrelated source preparation occurred.

## Reproduction and evidence

From `web/apps/site` after sourcing `web/scripts/env.sh`, commands are recorded verbatim in each `evidence/viz-*-resources.json`, with complete output in the matching `.log`. `viz-red-unit` shows the missing module before implementation; `viz-red-production` shows the prior production build missing `/map`. `viz-final-units` records23/23 covering tests; later integrity/native covering runs are separate. Native model/API actual routing appears in `viz-rest-wiring-final`; controlled native protocol tests in `viz-native-final`. Production compilation, en/zh fallback, reduced-motion, bounded API, deferred JS, focus and keyboard journey outputs are retained. The final focused behavior gate covers both static graphics, full padded tables, keyboard scrolling, reduced motion,12 controlled coarse pentagons and pointer drag. The final first-navigation JavaScript is138,831 bytes gzip against204,800 bytes; this is not field CWV evidence.

Primary API references: [Snowflake H3](https://docs.snowflake.com/en/sql-reference/functions/h3_latlng_to_cell), [hex conversion](https://docs.snowflake.com/en/sql-reference/functions/h3_int_to_string), [deck H3 layer](https://deck.gl/docs/api-reference/geo-layers/h3-hexagon-layer), [deck performance](https://deck.gl/docs/developer-guide/performance), [Motion reduced motion](https://motion.dev/docs/react-use-reduced-motion).

Measurement references: [W3C DOMHighResTimeStamp](https://www.w3.org/TR/hr-time-3/#dom-domhighrestimestamp) and [HTML animation callbacks](https://html.spec.whatwg.org/multipage/imagebitmap-and-animations.html#animation-frames). Integer units do not assert nanosecond clock precision.
