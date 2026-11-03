// Produce a single reviewed optional Cube aggregate; raw frequency rows stay server-side.
import { readFileSync, writeFileSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { aggregate, type Frequency } from '../apps/site/lib/viz-data.ts';
const dir = resolve('../../../.tools/viz/actual');
const manifest = JSON.parse(readFileSync(resolve(dir, 'manifest.json'), 'utf8'));
const bytes = readFileSync(resolve(dir, 'frequencies.json'));
if (createHash('sha256').update(bytes).digest('hex') !== manifest.sha256) throw Error('Projection integrity');
const doc = JSON.parse(bytes.toString());
const rows: Frequency[] = doc.rows.map((v: unknown[]) => Object.fromEntries(doc.columns.map((k: string, i: number) => [k, v[i]])));
const result = aggregate(rows, {
  resolution: 3
});
const data = JSON.stringify({
  ...result,
  sourceSnapshot: manifest.sourceSnapshot,
  projection: manifest.sha256
});
if (Buffer.byteLength(data) > 5000000) throw Error('Aggregate cap');
writeFileSync(resolve(dir, 'aggregate.json'), data, {
  flag: 'wx'
});
console.log(JSON.stringify({
  cells: result.cells.length,
  coverage: result.coverage,
  aggregateBytes: Buffer.byteLength(data)
}));
