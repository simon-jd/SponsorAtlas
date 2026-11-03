import { latLngToCell } from 'h3-js';
import { validId } from './data.ts';
import { VIZ_LIMITS } from './viz-limits.ts';
export type Scope = {
  resolution: number;
  year?: number;
  employer?: string;
};
export type Frequency = {
  employer: string | null;
  fiscal_year: number;
  employer_history_status: string | null;
  latitude: number | null;
  longitude: number | null;
  county_reference_vintage: string | null;
  coordinate_reference_vintage: string | null;
  coordinate_semantics: string | null;
  county_mapping_status: string | null;
  annual_wage: number | null;
  count: number;
};
export type Cell = {
  h3: string;
  count: number;
  wage_p50: number | null;
};
export type Aggregate = ReturnType<typeof aggregate>;
export function parseScope(params: URLSearchParams): Scope {
  for (const key of params.keys()) if (!['resolution', 'year', 'employer'].includes(key) || params.getAll(key).length !== 1) throw Error('Invalid scope');
  const resolution = Number(params.get('resolution') ?? 3);
  if (!Number.isInteger(resolution) || resolution < 0 || resolution > 7) throw Error('Invalid resolution');
  const year = params.has('year') ? Number(params.get('year')) : undefined;
  if (year !== undefined && (!Number.isInteger(year) || year < 1900 || year > 2200)) throw Error('Invalid year');
  const employer = params.get('employer') ?? undefined;
  if (employer !== undefined && !validId(employer)) throw Error('Invalid employer');
  return {
    resolution,
    ...(year === undefined ? {} : {
      year
    }),
    ...(employer === undefined ? {} : {
      employer
    })
  };
}
export function validatePoint(lat: unknown, lon: unknown): boolean {
  return typeof lat === 'number' && typeof lon === 'number' && Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
}
export function weightedMedian(values: [number, number][]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a[0] - b[0]);
  const n = sorted.reduce((s, v) => s + v[1], 0);
  const a = Math.floor((n - 1) / 2),
    b = Math.floor(n / 2);
  let sum = 0,
    lo: number | undefined,
    hi = 0;
  for (const [value, count] of sorted) {
    sum += count;
    if (lo === undefined && sum > a) lo = value;
    if (sum > b) {
      hi = value;
      break;
    }
  }
  return ((lo ?? 0) + hi) / 2;
}
export function aggregate(rows: Frequency[], scope: Scope) {
  parseScope(new URLSearchParams(Object.entries(scope).map(([k, v]) => [k, String(v)])));
  const cells = new Map<string, {
    count: number;
    wages: [number, number][];
  }>();
  const coverage = {
    total: 0,
    mapped: 0,
    unmapped: 0,
    nullWages: 0,
    history: {} as Record<string, number>,
    vintages: {} as Record<string, number>,
    mapping: {} as Record<string, number>
  };
  const histogram = Array.from({
    length: 11
  }, (_, i) => ({
    lower: i * 50000,
    upper: i === 10 ? null : (i + 1) * 50000,
    count: 0
  }));
  const add = (target: Record<string, number>, key: string, n: number) => {
    target[key] = (target[key] ?? 0) + n;
  };
  for (const row of rows) {
    if (scope.year !== undefined && row.fiscal_year !== scope.year || scope.employer !== undefined && row.employer !== scope.employer) continue;
    if (!Number.isSafeInteger(row.count) || row.count <= 0 || row.annual_wage !== null && (!Number.isFinite(row.annual_wage) || row.annual_wage < 0)) throw Error('Invalid frequency');
    const n = row.count;
    coverage.total += n;
    add(coverage.history, row.employer_history_status ?? 'UNKNOWN', n);
    add(coverage.vintages, `${row.county_reference_vintage ?? 'UNKNOWN'} → ${row.coordinate_reference_vintage ?? 'UNKNOWN'}`, n);
    add(coverage.mapping, row.county_mapping_status ?? 'UNKNOWN', n);
    if (row.annual_wage === null) coverage.nullWages += n;else histogram[Math.min(10, Math.floor(row.annual_wage / 50000))].count += n;
    if (!validatePoint(row.latitude, row.longitude)) {
      coverage.unmapped += n;
      continue;
    }
    coverage.mapped += n;
    const h3 = latLngToCell(row.latitude!, row.longitude!, scope.resolution);
    const cell = cells.get(h3) ?? {
      count: 0,
      wages: []
    };
    cell.count += n;
    if (row.annual_wage !== null) cell.wages.push([row.annual_wage, n]);
    cells.set(h3, cell);
    if (cells.size > VIZ_LIMITS.maxCells) throw Error('Scope exceeds cell limit');
  }
  return {
    cells: [...cells].map(([h3, v]) => ({
      h3,
      count: v.count,
      wage_p50: weightedMedian(v.wages)
    })).sort((a, b) => a.h3.localeCompare(b.h3)),
    histogram,
    coverage,
    resolution: scope.resolution
  };
}

// Strictly reconstruct the named native transport payload; never forward source rows.
export function parseNativeAggregate(raw: unknown, scope: Scope, identity: {
  source: string;
  version: string;
}) {
  const object = (v: unknown): Record<string, unknown> => {
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw Error('Invalid native object');
    return v as Record<string, unknown>;
  };
  const number = (v: unknown) => {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0) throw Error('Invalid native numeric');
    return v;
  };
  const count = (v: unknown) => {
    const n = number(v);
    if (!Number.isSafeInteger(n)) throw Error('Invalid native count');
    return n;
  };
  const data = object(raw),
    source = object(data.identity);
  if (source.source !== identity.source || source.version !== identity.version || data.kind !== 'COUNTY_INTERNAL_POINT_NOT_WORKSITE' || data.resolution !== scope.resolution) throw Error('Native identity mismatch');
  if (!Array.isArray(data.cells) || data.cells.length > VIZ_LIMITS.maxCells) throw Error('Native cell limit');
  const cells: Cell[] = data.cells.map(value => {
    const c = object(value);
    if (typeof c.h3 !== 'string' || !/^8[0-9a-f]{14}$/.test(c.h3) || Number(BigInt('0x' + c.h3) >> 52n & 15n) !== scope.resolution) throw Error('Invalid native cell');
    return {
      h3: c.h3,
      count: count(c.count),
      wage_p50: c.wage_p50 === null ? null : number(c.wage_p50)
    };
  });
  if (new Set(cells.map(c => c.h3)).size !== cells.length) throw Error('Duplicate native cell');
  const c = object(data.coverage);
  const codes = (value: unknown) => {
    const entries = Object.entries(object(value));
    if (entries.length > 1000 || entries.some(([k]) => k.length > 205)) throw Error('Native metadata limit');
    return Object.fromEntries(entries.map(([k, v]) => [k, count(v)]));
  };
  const coverage = {
    total: count(c.total),
    mapped: count(c.mapped),
    unmapped: count(c.unmapped),
    nullWages: count(c.nullWages),
    history: codes(c.history),
    vintages: codes(c.vintages),
    mapping: codes(c.mapping)
  };
  if (!Array.isArray(data.histogram) || data.histogram.length !== 11) throw Error('Native histogram limit');
  const histogram = data.histogram.map((value, i) => {
    const b = object(value);
    if (b.lower !== i * 50000 || b.upper !== (i === 10 ? null : (i + 1) * 50000)) throw Error('Invalid native band');
    return {
      lower: i * 50000,
      upper: i === 10 ? null : (i + 1) * 50000,
      count: count(b.count)
    };
  });
  if (coverage.total !== coverage.mapped + coverage.unmapped || cells.reduce((n, c) => n + c.count, 0) !== coverage.mapped || histogram.reduce((n, b) => n + b.count, 0) + coverage.nullWages !== coverage.total || [coverage.history, coverage.vintages, coverage.mapping].some(group => Object.values(group).reduce((n, v) => n + v, 0) !== coverage.total)) throw Error('Native coverage mismatch');
  return {
    cells,
    histogram,
    coverage,
    resolution: scope.resolution,
    provenance: {
      kind: data.kind,
      source: identity.source,
      version: identity.version,
      projection: null
    }
  };
}
