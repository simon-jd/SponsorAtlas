// Distinct H3 cells per scope (no cell limit), to size VIZ_LIMITS.maxCells.
// usage: node web/scripts/viz-cells-measure.mjs <projection dir> web/apps/site
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';
const [dir, site] = process.argv.slice(2);
const { latLngToCell } = createRequire(resolve(site, 'package.json'))('h3-js');
const doc = JSON.parse(readFileSync(resolve(dir, 'frequencies.json'), 'utf8'));
const c = Object.fromEntries(doc.columns.map((n, i) => [n, i]));
const out = [];
for (const year of [undefined, 2024, 2025]) for (const resolution of [5, 6, 7]) {
  const cells = new Set();
  for (const r of doc.rows) {
    if (year !== undefined && r[c.fiscal_year] !== year) continue;
    if (typeof r[c.latitude] !== 'number' || typeof r[c.longitude] !== 'number') continue;
    cells.add(latLngToCell(r[c.latitude], r[c.longitude], resolution));
  }
  out.push({ year: year ?? 'all', resolution, cells: cells.size });
}
console.log(JSON.stringify(out));
