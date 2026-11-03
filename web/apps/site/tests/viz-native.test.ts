import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nativeVisualization } from '../lib/viz-native.ts';
const payload = {
  cells: [{
    h3: '832830fffffffff',
    count: 1,
    wage_p50: null
  }],
  histogram: Array.from({
    length: 11
  }, (_, i) => ({
    lower: i * 50000,
    upper: i === 10 ? null : (i + 1) * 50000,
    count: 0
  })),
  coverage: {
    total: 2,
    mapped: 1,
    unmapped: 1,
    nullWages: 2,
    history: {
      UNKNOWN: 2
    },
    vintages: {
      'UNKNOWN → UNKNOWN': 2
    },
    mapping: {
      UNKNOWN: 2
    }
  },
  resolution: 3,
  kind: 'COUNTY_INTERNAL_POINT_NOT_WORKSITE',
  identity: {
    source: 'snowflake',
    version: 'controlled-v1'
  }
};
test('native site transport sends only named scope/auth/source identity; strict response reconstruction and cache separation', async () => {
  const old = {
    ...process.env
  };
  Object.assign(process.env, {
    SITE_DATA_SOURCE: 'snowflake',
    SITE_SOURCE_VERSION: 'controlled-v1',
    CUBEJS_API_SECRET: 'controlled-secret-at-least-32-characters',
    CUBE_REST_URL: 'http://127.0.0.1:4100/cubejs-api/v1/load'
  });
  try {
    let sent = 0;
    const transport: typeof fetch = async (url, options) => {
      sent++;
      assert.equal(String(url), 'http://127.0.0.1:4100/cubejs-api/v1/h3');
      assert.equal(options?.cache, 'no-store');
      assert.equal(new Headers(options?.headers).get('X-SponsorAtlas-Data-Version'), 'controlled-v1');
      assert.match(new Headers(options?.headers).get('Authorization') || '', /^[\w-]+\.[\w-]+\.[\w-]+$/);
      assert.deepEqual(JSON.parse(String(options?.body)), {
        resolution: 3,
        year: 2025,
        employer: 'A'
      });
      return Response.json({
        ...payload,
        private_rows: 'must not forward'
      });
    };
    const result = await nativeVisualization({
      resolution: 3,
      year: 2025,
      employer: 'A'
    }, transport);
    assert.equal(result.coverage.unmapped, 1);
    assert.equal(result.cells[0].wage_p50, null);
    assert.equal(JSON.stringify(result).includes('private_rows'), false);
    assert.equal(sent, 1);
    process.env.SITE_SOURCE_VERSION = 'controlled-v2';
    await assert.rejects(nativeVisualization({
      resolution: 3
    }, async () => Response.json(payload)), /identity/);
    await assert.rejects(nativeVisualization({
      resolution: 3
    }, async () => new Response('x'.repeat(2000001))), /byte bound/);
    process.env.SITE_SOURCE_VERSION = 'controlled-v1';
    await assert.rejects(nativeVisualization({
      resolution: 3
    }, async () => Response.json({
      ...payload,
      coverage: {
        ...payload.coverage,
        total: 999
      }
    })), /coverage/);
  } finally {
    process.env = old;
  }
});
