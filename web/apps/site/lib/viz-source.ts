import 'server-only';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { dataIdentity } from './cube-settings.ts';
import { aggregate, type Frequency, type Scope } from './viz-data.ts';
import { VIZ_LIMITS } from './viz-limits.ts';
// Local projection: Census2020 worksite ZCTA/place internal points (semantic/scripts/export_worksite_coordinates.py).
export const WORKSITE_PROJECTION_KIND = 'WORKSITE_ZCTA_OR_PLACE_INTERNAL_POINT';
let loaded: {
  key: string;
  rows: Frequency[];
  manifest: Record<string, unknown>;
} | undefined;
const cache = new Map<string, ReturnType<typeof aggregate>>();
export function visualization(scope: Scope) {
  const identity = dataIdentity(process.env);
  if (identity.source === 'snowflake') throw Error('Native coordinate source requires separately validated opt-in adapter');
  const packaged=process.env.SITE_PACKAGED_PROJECTION==='1';
  const configured=process.env.SITE_VIZ_DIRECTORY;
  if(packaged&&(configured||identity.source!=='parquet'))throw Error('Packaged projection requires its own immutable Parquet directory');
  if(configured && (!configured.startsWith('/') || identity.source==='fixture'))throw Error('Invalid deployment projection directory');
  const dir = packaged ? resolve(process.cwd(),'deployment-data/viz') : configured || resolve(process.cwd(), '../../../.tools/viz', identity.source === 'fixture' ? 'fixture' : 'worksite-fy2025');
  const manifestFile=resolve(dir,'manifest.json');const manifestStat=statSync(manifestFile);
  if(manifestStat.size>10000||(manifestStat.mode&0o222)!==0)throw Error('Invalid immutable manifest');
  const manifest = JSON.parse(readFileSync(manifestFile, 'utf8'));
  if (manifest.sourceSnapshot !== identity.version || manifest.kind !== (identity.source === 'fixture' ? 'CONTROLLED_COORDINATE_FIXTURE' : WORKSITE_PROJECTION_KIND)) throw Error('Visualization source identity mismatch');
  const key = JSON.stringify([identity, manifest.sha256]);
  if (loaded?.key !== key) {
    const file = resolve(dir, 'frequencies.json');
    if (statSync(file).size > VIZ_LIMITS.maxFrequencyBytes || (statSync(file).mode & 0o222) !== 0) throw Error('Invalid visualization artifact');
    const bytes = readFileSync(file);
    if (bytes.length !== manifest.bytes || createHash('sha256').update(bytes).digest('hex') !== manifest.sha256) throw Error('Visualization integrity mismatch');
    const document = JSON.parse(bytes.toString());
    if (!Array.isArray(document.rows) || document.rows.length > VIZ_LIMITS.maxFrequencyRows || !Array.isArray(document.columns)) throw Error('Projection limit');
    const rows: Frequency[] = document.rows.map((values: unknown[]) => Object.fromEntries(document.columns.map((name: string, i: number) => [name, values[i]])));
    loaded = {
      key,
      rows,
      manifest
    };
    cache.clear();
  }
  const queryKey = JSON.stringify([key, scope]);
  let result = cache.get(queryKey);
  if (!result) {
    result = aggregate(loaded.rows, scope);
    if (cache.size >= 16) cache.delete(cache.keys().next().value!);
    cache.set(queryKey, result);
  }
  return {
    ...result,
    provenance: {
      kind: manifest.kind,
      projection: manifest.sha256,
      source: identity.source,
      version: identity.version
    }
  };
}
