# SponsorAtlas frontend workspace

The frontend workspace: `apps/site` is the Next.js 16 site (see its README) and `packages/ui` holds the design tokens and seven accessible React components with Storybook tests. No native Figma file was made; see `../design/figma.md`.

## Rebuild locally

Requires the repository's Node24.22.1 runtime at `.tools/node-runtime/node-v24.22.1-linux-x64/bin/node`, Python3, and existing `/usr/bin/google-chrome`. The bootstrap pins pnpm12.15.0 with npm integrity lock metadata; only this machine's Linux/x64 glibc executable is selected. No global install or browser download is needed.

```bash
bash web/scripts/bootstrap.sh
bash web/scripts/test-local.sh
```

The second command generates tokens, checks real Tailwind compilation, typechecks, builds static Storybook, serves only127.0.0.1:6006, runs the actual `pnpm -F ui test-storybook` browser gate, then closes its server and private SWC cache. It refuses an already occupied port. All ordinary cache/store/config/profile files are under `web/.cache` or `web/.tools`; SWC alone uses an atomically created0700 `/tmp/sponsoratlas-task12-swc.*` directory because its security validation rejects this workspace's replaceable ancestors. That directory is deleted by the command's trap. Budget it with the repository even though it is in `/tmp`.

For individual commands:

```bash
cd web
source scripts/env.sh
pnpm -F ui tokens
pnpm -F ui typecheck
pnpm -F ui test-tokens
pnpm -F ui build-storybook
node scripts/serve-storybook.mjs
# In another similarly initialized shell, with a safe SWC_NATIVE_BINDING_CACHE:
pnpm -F ui test-storybook
```

`pnpm-lock.yaml` is the runtime dependency lock. `package-lock.json` records the original metadata-only estimate; it is not an alternative install workflow. `graph-estimate.json` includes all platform metadata (791 versions), while installation selects compatible packages (617). `resource-plan.json` is the controller-checked2.25GB incremental peak bound; do not automatically install browsers or broaden platform selection. `scripts/run-monitored.py LABEL COMMAND...` records sampled descendant RSS and test profile paths and stops the command if its6GB/180s bounds are exceeded.

## Next.js / RSC integration contract

The package name is `ui`. Add `"ui": "workspace:*"` to the future `web/apps/site` package and `transpilePackages: ['ui']` to Next.js configuration. Source exports preserve per-component `use client` boundaries and expose TypeScript interfaces; the package is ESM and marks only CSS as side effects. Import global `ui/styles.css` once in the site root layout. Set `data-theme="light"` or `data-theme="dark"` on the root; resolve the initial theme on the server to avoid a flash. No provider, icon pack, browser global during render, data client or English UI label is required.

```tsx
import { Button, DataTable, type DataColumn } from 'ui';
import 'ui/styles.css';
```

Interactive callbacks (`onClick`, `onValueChange`, table render/comparator functions) belong inside a client adapter, not serialized from a Server Component into a client boundary. RSCs may fetch/format public data and pass serializable rows and localized strings to that adapter. Chart/map slots accept React content, but graphics, summaries, value formatting and locality are caller responsibilities. Components do not fetch or infer private data.

| Export | Required data and labels | State / keyboard contract |
| --- | --- | --- |
| `Button`, `ButtonProps` | Native button props including visible children | `variant: primary\|secondary`; default `type=button`; native disabled and Enter/Space behavior. |
| `Combobox`, `ComboboxProps`, `ComboboxOption` | `label, options[{value,label,disabled?}], value, onValueChange, emptyMessage` | Controlled selected value; unique nonempty option values, empty string means none. Typing clears committed selection and filters locally; optional placeholder/error/disabled. Escape/blur restore the currently committed label. Up/Down, Home/End while open, Enter, Escape, Tab. |
| `DataTable`, `DataTableProps<Row>`, `DataColumn<Row>` | `caption, columns[{key,header,render,compare?}], rows, rowKey, emptyMessage` | Unique column/row keys, at least one column; optional comparator makes a header sortable. Internal ascending/descending toggles, stable native sort; no pagination/virtualization/server-sort semantics. Native table navigation; focusable overflow region and header buttons. |
| `ChartFrame`, `ChartFrameProps` | `title, description, dataLabel, data`; optional children/status | Required accessible data alternative, optional graphic. A supplied status replaces graphic; disclosure stays reachable. |
| `MapContainer`, `MapContainerProps` | `title, description, fallbackLabel, fallback`; optional children/status | Required non-graphical alternative; same status/disclosure behavior. No location permission or map engine. |
| `Tooltip`, `TooltipProps` | `label, content` strings | Visible button trigger; supplemental non-interactive text. Focus/hover/Escape, no focus transfer. |
| `Tabs`, `TabsProps`, `TabItem` | `label, items[{id,label,content,disabled?}]` | Unique IDs and at least one enabled tab. Optional controlled value/onValueChange or defaultValue. Horizontal automatic activation; disabled items skipped; active panel tabbable. |

## Tokens and Tailwind4

`design/tokens.json` is repository-authored, not a native Figma export. Style Dictionary5 emits deterministic `src/generated/tokens.css` with matching semantic names for both themes. Font/space/radius/motion values are shared. Motion milliseconds are normalized to equivalent seconds. Import `ui/tokens.css` alone if you do not want base component styles.

Tailwind4 consumers import the generated `ui/tailwind.css` after Tailwind and token CSS:

```css
@import "tailwindcss";
@import "ui/tokens.css";
@import "ui/tailwind.css";
```

The generated `@theme inline` maps semantic namespaces to CSS variables: `bg-surface`, `text-ink`, `p-4`, `text-lg`, `rounded-md`, `duration-fast` all use the same source as the components. Tests compile these classes with the real installed Tailwind compiler and assert computed values in both browser themes plus reduced motion. `preview-utilities.css` is only a test fixture, not an exported product dependency. No Tailwind runtime is required by components themselves.

## Test implementation and limits

Every named component has stories and real Playwright keyboard/focus assertions in `packages/ui/tests/browser-contract.cjs`, executed by Storybook's test-runner. Axe is injected and failures throw at initial and relevant open/focused/empty/error/disabled states. The a11y addon also uses `test: 'error'`. There are no skipped stories or mocked browser controls in the full gate. Set `UI_TEST_STORY=Combobox` (or another story basename) before the same test command for a focused regression run. Narrow assertions verify no viewport overflow while tables have their own scroll area.

Storybook10.6's config loader calls `module.register`, which Jest30 disallows in its sandbox. The supported Jest `setupFilesAfterEnv` extension loads the plain CJS hook module and calls the runner's exported `setPreVisit`/`setPostVisit`; third-party code and safety checks are unchanged. Explicit `testEnvironmentOptions` selects system Chrome and isolated temporary profiles. This is repository test automation, not product-browser automation against a personal profile.

Official support for the test-runner has ended. Its warning is retained, and the exact planned command is verified on this lockfile; that is not a promise of future compatibility. A later maintenance decision should evaluate the official Vitest migration. Automated component checks do not establish full WCAG2.2AA, native Figma acceptance, site performance/RUM or first-screen JS compliance.
