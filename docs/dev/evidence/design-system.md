# Task12 design-system evidence — local implementation verified; native acceptance OPEN

Date:2026-10-24. Scope: repository-authored tokens, reusable components, Storybook contracts and local page references. No native Figma document or native token export was produced. The existing extension blocker was not bypassed or retried. Task11 native acceptance is separate and remains OPEN.

## Executed checks

| Gate | Result / retained evidence |
| --- | --- |
| Required TDD RED | `cd web; pnpm -F ui test-storybook` failed7/7 suites,20/20 initial component stories before implementation. Missing buttons/comboboxes/tables/regions/tooltip triggers/tabs caused real browser failures. `web/evidence/storybook-red.log`. |
| Required GREEN | The same command passed9/9 suites,28/28 stories with actual headless system Chrome155.0.8059.84, one worker. `web/evidence/storybook-green.log`. |
| Token pipeline | `pnpm -F ui tokens`; `pnpm -F ui test-tokens`:2/2. Initial RED was missing generated CSS. Real Style Dictionary output and real Tailwind4 compilation are tested; browser light/dark fixtures assert color, spacing, type, radius and reduced-motion computed styles. |
| Type interfaces | `pnpm -F ui typecheck`:pass, TypeScript7.0.2. |
| Rebuild | `pnpm install --frozen-lockfile --ignore-scripts`:pass; `pnpm -F ui build-storybook`:pass. Pinned package/lock integrity metadata retained. |
| Workflow lifecycle | `bash scripts/test-local.sh`:exit0; token checks, typecheck, build, loopback server,28/28 browser tests, server/cache trap cleanup. `web/evidence/local-workflow.log`. |
| Self-review regression | Up from a typed query with no active combobox option originally chose the wrong entry. Focused new test was RED3/5, fix GREEN5/5 using `UI_TEST_STORY=Combobox pnpm -F ui test-storybook`; final full suite includes this check. |

The test-runner labels hook-based tests `smoke-test`; the hook implementation in `web/packages/ui/tests/browser-contract.cjs` performs actual keyboard, focus, role, sort, disclosure and axe assertions. These are not render-only checks. Axe checks initial and relevant open/focus/empty/disabled/error/theme states for WCAG2A/AA,2.1AA and2.2AA tags; zero violations in the tested regions. The addon also uses `a11y.test='error'`. Tests fail rather than treating findings as advisory.

The native-table contract uses caption/headers, buttons with `aria-sort`, and a focusable horizontal-scroll region. The narrow story proves ArrowRight changes actual `scrollLeft`, Tab reaches the first sort button, and Enter toggles sort direction/order. Screen-reader cell navigation stays native; no `grid` role or arrow-cell navigation is claimed. Chart/map regions have actual names/descriptions and keyboard-operable native disclosures containing required data alternatives.

## Visual evidence

`web/evidence/screenshots/` contains isolated test-browser captures: `reference-explore.png`, `reference-employer.png`, `reference-compare.png` (dark), `reference-narrow.png` (390px), `combobox-open-keyboard.png`, `combobox-open-dark.png`, and `tabs-narrow-focus.png`. The implementer inspected these artifacts, and the controller separately inspected six captures. They show coherent hierarchy, visible disclaimers and illustrative-data labels, focus rings, themes, and narrow stacking. Narrow table overflow is confined to its keyboard-scrollable region. These captures are not native Figma evidence or human testing of the eventual site.

## Tooling and resource evidence

Exact pins include Node24.22.1, pnpm12.15.0, Storybook/react-vite/a11y10.6.3, test-runner0.26.0, React19.3.1, Vite8.3.5, Style Dictionary5.7.1 and Tailwind4.3.3. The metadata graph includes791 versions/all platforms, with434,159,830 reported Linux/x64 unpacked bytes before platform/libc selection and52 older packages lacking size fields. Only617 compatible packages were installed; bootstrap installed only the glibc pnpm executable. No Playwright browser download occurred.

The controller checked `web/resource-plan.json`:2.25GB incremental peak envelope including store, node_modules, caches, browser/test temp, build, source/evidence and Git;6GB RSS target. The metadata-only npm cache (~420MB) was removed before installation. Configured ordinary storage roots are within `web`, but successful build logs also record a failed attempt to create `/home/postedism/.storybook/settings.json` (ENOENT). That attempted HOME write is unresolved/deferred M1; these logs do not prove a successful HOME write. SWC's validated temporary-cache exception used `/tmp/sponsoratlas-task12-swc.4oDLP6` (owner postedism,0700,~30.75MB), later removed. The one-command workflow atomically creates and removes its own equivalent private cache. No ancestor permissions or third-party security checks were modified.

Sampled whole-workflow descendant RSS peaked at2,220,445,696B (200ms samples); this is sampled evidence, not a continuous physical-memory maximum. `web/evidence/*-resources.json` records commands, exits, sampling and observed test PIDs. The controller's complete installed-stage point was14,696,950,775B including the SWC cache, below18e9; it is a point measurement, not a final/peak claim. Final cleanup/process and storage figures are in the implementation report and controller ledger.

Infrastructure startup failures were corrected before behavioral RED: pnpm12 settings moved to its workspace/env format; dev watcher EMFILE avoided with static Storybook; SWC safe-cache validation honored via the private `/tmp` exception; explicit Jest launch settings selected existing Chrome; Jest cache was made absolute/project-local. Storybook10.6's loader calls `module.register`, rejected inside Jest30. The configured Jest `setupFilesAfterEnv` plus the runner's exported `setPreVisit`/`setPostVisit` loads the same assertions without modifying dependencies or disabling checks.

## Original acceptance and remaining limits

Native Figma Explore/Employer/Compare/component pages and actual native token export remain required and OPEN. Local references and repository-authored tokens do not close them. Automated component evidence does not establish all WCAG2.2AA criteria, assistive-technology usability, site p75 LCP≤2.5s/INP≤200ms/CLS≤0.1, first-screen JS≤200KB gzip, actual RUM, or Task14 WebGL/frame performance. Storybook's development/gallery bundle is not the site bundle.

The official Storybook test-runner page now states support has ended; its maintenance warning is retained in logs. The locked stack works here, but future upgrades need compatibility review or a separately scoped Vitest migration. Build warnings about client directives in a browser-only Storybook bundle are retained; source component entry points preserve those directives for later Next.js/RSC use. Reference employer names are illustrative only; the later public API's stable employer IDs must drive real integration.

## Sources and handoff

- [Storybook test-runner documentation](https://storybook.js.org/addons/@storybook/test-runner): lifecycle hooks, Jest configuration, system-browser options and current support warning; installed package `playwright/jest-setup.js` and `dist/index.js` verify exported hook integration.
- [Storybook accessibility testing](https://storybook.js.org/docs/writing-tests/accessibility-testing): failing a11y gate behavior.
- [Style Dictionary built-in formats](https://styledictionary.com/reference/hooks/formats/predefined/): CSS variable format/selector; Tailwind theme is a local generated adapter verified by compilation and computed styles.
- [WAI-ARIA Combobox](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/), [Table](https://www.w3.org/WAI/ARIA/apg/patterns/table/), [Tabs](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/), [Tooltip](https://www.w3.org/WAI/ARIA/apg/patterns/tooltip/): interaction patterns.
- [pnpm configuration](https://pnpm.io/cli/config): version12 workspace/settings behavior.

API/token contracts and rebuild commands: `web/README.md`. Native handoff/component specification: `design/figma.md`. All product source edits remain within `design/**`, `web/**` and this evidence document.

## Review fix round1 — monitor lifecycle (I2)

The monitor now handles SIGINT/SIGTERM through a final cleanup/evidence path, detects command exit without discarding the leader wait status, and adopts orphaned descendants with the Linux child-subreaper API. Ownership includes children that enter a separate session; process identity-checked pidfds target only owned processes. It re-scans during a bounded TERM grace (1s default), escalates to KILL (1s default grace), and bounds the final leader wait (1s default). Adopted descendants are reaped; any remaining PID produces a cleanup-incomplete failure rather than a success claim. Original stdout and return status remain intact for normal commands. SIGKILL of the monitor cannot run a finally block; an uninterruptible kernel task may survive the bounded wait and is reported, not claimed cleaned.

Direct harmless subprocess coverage: `python3 -m unittest discover -s web/scripts/tests -p 'test_run_monitored.py' -v`. Initial RED had6 failing/error cases; a separate detached-child regression then demonstrated the inadequate same-group-only approach. Final GREEN is9/9, covering SIGINT, SIGTERM, exited leader with live descendant, detached child, TERM resistance/KILL, timeout, RSS limit, spawn error and normal exit/output. `web/evidence/monitor-fix1-{red,detached-red,green}.log` and `monitor-fix1-cases.json` retain outputs and final outcomes. Tests assert no fixture PID remains, including zombies, and delete their own temporary directories. No browser, Storybook, dependency install, native UI, cloud or source-data suite was run for this fix.

I1/native Figma remains OPEN. M1–M4 remain deferred for final review; this fix does not claim HOME writes were never attempted, resolve unsupported/deprecated tooling, or suppress gallery/a11y diagnostics.
