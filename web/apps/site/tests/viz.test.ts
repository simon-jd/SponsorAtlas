import test from 'node:test';
import assert from 'node:assert/strict';
import { aggregate, parseScope, validatePoint, weightedMedian, type Frequency } from '../lib/viz-data.ts';
import { VIZ_LIMITS } from '../lib/viz-limits.ts';
const row = (wage: number | null, count = 1, lat: number | null = 37.7, lon: number | null = -122.4): Frequency => ({
  employer: 'A',
  fiscal_year: 2025,
  employer_history_status: 'PRE_SNAPSHOT_HISTORY_UNKNOWN',
  latitude: lat,
  longitude: lon,
  county_reference_vintage: 'Census2020',
  coordinate_reference_vintage: 'Census2023',
  coordinate_semantics: 'COUNTY_INTERNAL_POINT_NOT_WORKSITE',
  county_mapping_status: 'MATCHED',
  annual_wage: wage,
  count
});
test('exact weighted median uses eligible rows, never county medians or NULL zero', () => {
  assert.equal(weightedMedian([[10, 1], [20, 4], [1000, 1]]), 20);
  assert.equal(weightedMedian([[10, 1], [20, 1]]), 15);
  assert.equal(weightedMedian([]), null);
});
test('scope rejects unsafe resolution, identifier, fiscal period and unknown parameters', () => {
  for (const s of ['resolution=16', 'resolution=NaN', 'resolution=2.5', 'employer=UNKNOWN', 'year=1800', 'sql=select']) assert.throws(() => parseScope(new URLSearchParams(s)));
  assert.deepEqual(parseScope(new URLSearchParams('year=2025&employer=A&resolution=4')), {
    resolution: 4,
    year: 2025,
    employer: 'A'
  });
});
test('coordinate validation rejects wrapping/nonfinite/missing values', () => {
  for (const p of [[91, 0], [0, 181], [NaN, 0], [null, 0], [0, null]]) assert.equal(validatePoint(p[0], p[1]), false);
  assert.equal(validatePoint(90, 180), true);
});
test('aggregate preserves count, missing coordinates, wage NULL and source vintage; filters before math', () => {
  const rows = [row(10), row(20, 4, 37.71, -122.41), row(1000), row(null, 2), row(999, 3, null, null), {
    ...row(9999),
    employer: 'B'
  }, {
    ...row(9999),
    fiscal_year: 2024
  }];
  const result = aggregate(rows, parseScope(new URLSearchParams('year=2025&employer=A&resolution=3')));
  assert.equal(result.coverage.total, 11);
  assert.equal(result.coverage.mapped, 8);
  assert.equal(result.coverage.unmapped, 3);
  assert.equal(result.coverage.nullWages, 2);
  assert.equal(result.cells.length, 1);
  assert.deepEqual(Object.keys(result.cells[0]).sort(), ['count', 'h3', 'wage_p50']);
  assert.equal(result.cells[0].count, 8);
  assert.equal(result.cells[0].wage_p50, 20);
  assert.equal(result.histogram.reduce((n, b) => n + b.count, 0), 9);
  assert.equal(result.coverage.history.PRE_SNAPSHOT_HISTORY_UNKNOWN, 11);
  assert.equal(result.coverage.vintages['Census2020 → Census2023'], 11);
});
test('coarse cells/pentagon coordinates remain valid strings at one requested resolution', () => {
  const result = aggregate([row(1, 1, 64.70000012793487, 10.536199075467675), row(2, 1, -90, 0)], {
    resolution: 0
  });
  assert.equal(result.cells.length, 2);
  assert.ok(result.cells.every(c => typeof c.h3 === 'string' && /^[0-9a-f]{15}$/.test(c.h3)));
});
test('one scope may return up to the shared cell limit, sized for the full-year projection', () => {
  // 130 x 130 grid, 0.05 degrees apart: one distinct resolution-7 cell per point.
  const grid = (n: number) => Array.from({length: n}, (_, i) => row(100000, 1, 30 + Math.floor(i / 130) * 0.05, -100 + (i % 130) * 0.05));
  assert.ok(VIZ_LIMITS.maxCells >= 12701 && 130 * 130 > VIZ_LIMITS.maxCells);
  assert.equal(aggregate(grid(12701), {resolution: 7}).cells.length, 12701);
  assert.throws(() => aggregate(grid(130 * 130), {resolution: 7}), /cell limit/);
});
