// Server-side cost of a map projection: load + verify + parse as lib/viz-source.ts does, then
// lib/viz-data.ts aggregate() per scope, 3 uncached runs each; prints RSS/heap and timings.
// usage: node --conditions=react-server web/scripts/viz-server-measure.mjs <projection dir> web/apps/site
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { cpus, loadavg } from 'node:os';

const [dir, site] = process.argv.slice(2);
const { aggregate } = await import(resolve(site, 'lib/viz-data.ts'));
const mb = (n) => Math.round(n / 1e6);
const mem = () => { const m = process.memoryUsage(); return { rssMB: mb(m.rss), heapUsedMB: mb(m.heapUsed), externalMB: mb(m.external) }; };
const out = { nproc: cpus().length, loadavgStart: loadavg(), baseline: mem() };
let t = performance.now();
const manifest = JSON.parse(readFileSync(resolve(dir, 'manifest.json'), 'utf8'));
const bytes = readFileSync(resolve(dir, 'frequencies.json'));
if (createHash('sha256').update(bytes).digest('hex') !== manifest.sha256) throw Error('sha');
const doc = JSON.parse(bytes.toString());
const rows = doc.rows.map((values) => Object.fromEntries(doc.columns.map((name, i) => [name, values[i]])));
out.load = { ms: Math.round(performance.now() - t), rows: rows.length, bytes: bytes.length, ...mem() };
const scopes = [
  ...[0, 1, 2, 3, 4, 5, 6, 7].map((resolution) => ({ resolution })),
  { resolution: 3, year: 2025 },
  { resolution: 3, year: 2024 },
  { resolution: 5, employer: rows.find((r) => r.employer)?.employer },
];
out.scopes = [];
for (const scope of scopes) {
  const runs = [];
  let result, error;
  for (let i = 0; i < 3; i++) {
    t = performance.now();
    try { result = aggregate(rows, scope); } catch (e) { error = String(e.message); }
    runs.push(Math.round(performance.now() - t));
  }
  out.scopes.push({ scope: { ...scope, employer: scope.employer ? 'first-employer' : undefined }, ms: runs, error,
    cells: result?.cells.length, total: result?.coverage.total, responseBytes: result ? Buffer.byteLength(JSON.stringify(result)) : undefined, ...mem() });
  result = undefined; error = undefined;
}
out.peakRssMB = mb(process.resourceUsage().maxRSS * 1024);
out.loadavgEnd = loadavg();
console.log(JSON.stringify(out, null, 1));
