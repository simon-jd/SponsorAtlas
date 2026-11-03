# SponsorAtlas design handoff — native Figma acceptance OPEN

No native Figma document was created or edited for Task12, and no native Figma token export occurred. Browser automation is blocked by the existing extension condition; this work did not retry third-party UI, bypass it, install a plugin, or invent a Figma URL. `tokens.json` is the repository-authored token source. Local Storybook references are independent deliverables, not substitutes for the required native originals.

## Native deliverable still required

Create a native Figma document with Explore, Employer detail, Compare, and component specification pages. Transfer these reviewed local values/layouts, record the actual file URL, demonstrate light/dark and narrow frames, and reconcile an actual native token export against `tokens.json`. A later authorized native session must supply evidence before this acceptance item closes.

## Local design reference

Run `web/scripts/test-local.sh` after bootstrap, or serve the built Storybook on `127.0.0.1:6006`. Under **Design / References**, inspect Explore, Employer, Compare (dark), and Narrow (390px). Source: `web/packages/ui/src/stories/References.stories.tsx`. The Storybook browser tests write screenshots to `web/evidence/screenshots/`; their names identify the views. All employer names/counts/wages are illustrative fixtures, not queried source data.

Explore establishes the search→results hierarchy: disclosure context, employer search, three summary metrics, sortable results, filing trend, regional fallback, and a persistent limitation statement. Employer detail leads with identity and period, followed by evidence tabs and data alternatives. Compare aligns metrics for a shared period, and explains missing-data and approval limitations. Production navigation, fetching, localization and real data integration belong to Task13; aggregated WebGL belongs to Task14.

## Component specification

| Component | Visual and interaction specification |
| --- | --- |
| Button | Solid teal primary or outlined secondary,44px minimum height, disabled native button, visible3px focus ring. Enter/Space activate. |
| Combobox | Visible label, bordered input, list popup with active/selected/disabled distinction. Editable list autocomplete; input retains focus. Up/Down navigate enabled options, Home/End move within an open list, Enter commits, Escape closes, Tab exits. Error text is linked; empty results announce. |
| DataTable | Native caption/column headers, horizontal scrolling at narrow widths, sortable header buttons and `aria-sort`. Tab visits scroll container/buttons/row controls; Enter/Space sorts. Screen readers retain native table navigation. No interactive-grid promise. |
| ChartFrame | Named region, heading and description, graphic slot, status fallback and native disclosure containing accessible data. Enter/Space opens disclosure without moving focus. |
| MapContainer | Named region and description, aggregated-map slot, status fallback and region-list disclosure. Same disclosure keyboard contract; no canvas or WebGL shipped here. |
| Tooltip | Supplemental text attached to a visible button. Focus/hover opens; Escape dismisses. Hovering tooltip preserves it; pointer exit does not dismiss while the trigger remains focused. Important guidance stays in persistent content. |
| Tabs | Horizontal automatic activation with roving focus; Left/Right wrap enabled tabs, Home/End move to edges, Tab enters the active panel. Disabled tabs are skipped. Panels must be immediately available. |

## Design system

System sans typography avoids a remote font dependency. A16px base uses14/16/20/24/32/48px sizes. Spacing follows4/8/12/16/24/32/48/64px. Rounded corners use6/12/20px. Semantic colors separate background, surface, ink, muted text, border, accent, accent text, focus and error in both themes. A3px offset focus ring and44px control heights make keyboard targets visible. Motion uses120/200ms and becomes0 with reduced motion. At narrow widths the reference cards stack; only data tables scroll horizontally. Tokens are canonical in `tokens.json`; generated values must never be edited by hand.

Persistent disclaimer: **LCA applications are not approvals and do not mean a position necessarily offers sponsorship.** The later localized site must retain the same meaning in en/zh.

## Boundaries

Automated axe/keyboard tests and isolated headless Chrome screenshots are component evidence, not human usability/screen-reader certification or proof of full WCAG2.2AA. Site p75 LCP≤2.5s, INP≤200ms, CLS≤0.1 and first-screen JS≤200KB gzip remain site-level gates. Storybook bundle size does not measure the site budget. Task11 native dashboard acceptance remains separately OPEN.
