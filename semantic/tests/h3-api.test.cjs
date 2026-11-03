const {
  test
} = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const jwt = require('jsonwebtoken');
const {
  handler
} = require('../h3-api.cjs');
const {
  nativeAggregate,
  nativeCoverageSql
} = require('../h3.cjs');
const coverage = [{
  HISTORY: 'PRE_SNAPSHOT_HISTORY_UNKNOWN',
  COUNTY_VINTAGE: 'Census2020',
  COORDINATE_VINTAGE: 'Census2023',
  MAPPING: 'MATCHED',
  WAGE_BAND: 0,
  MAPPED: 1,
  COUNT: '6'
}, {
  HISTORY: 'PRE_SNAPSHOT_HISTORY_UNKNOWN',
  COUNTY_VINTAGE: 'Census2020',
  COORDINATE_VINTAGE: 'Census2023',
  MAPPING: 'MATCHED',
  WAGE_BAND: -1,
  MAPPED: 1,
  COUNT: '2'
}, {
  HISTORY: 'PRE_SNAPSHOT_HISTORY_UNKNOWN',
  COUNTY_VINTAGE: null,
  COORDINATE_VINTAGE: null,
  MAPPING: 'UNRESOLVED',
  WAGE_BAND: 0,
  MAPPED: 0,
  COUNT: '3'
}];
const cells = [{
  H3: '832830fffffffff',
  COUNT: '8',
  WAGE_P50: '20'
}];
test('native canonical aggregate scopes both queries, preserves NULL/unmapped/vintages, rejects truncation', async () => {
  const queries = [];
  const driver = {
    query: async sql => {
      queries.push(sql);
      return sql.includes('H3_LATLNG') ? cells : coverage;
    }
  };
  const result = await nativeAggregate(driver, {
    resolution: 3,
    year: 2025,
    employer: 'A'
  });
  assert.equal(result.coverage.total, 11);
  assert.equal(result.coverage.mapped, 8);
  assert.equal(result.coverage.unmapped, 3);
  assert.equal(result.coverage.nullWages, 2);
  assert.equal(result.histogram[0].count, 9);
  assert.equal(result.cells[0].wage_p50, 20);
  for (const q of queries) {
    assert.match(q, /f.fiscal_year=2025/);
    assert.match(q, /f.employer_id='A'/);
    assert.match(q, /LIMIT 10001$/);
  }
  assert.match(nativeCoverageSql({
    resolution: 3,
    year: null,
    employer: null
  }), /IS NULL THEN -1/);
  await assert.rejects(nativeAggregate({
    query: async () => Array(10001).fill(cells[0])
  }, {
    resolution: 3
  }));
});
test('named native HTTP endpoint checks authentication/source identity/scope before any driver dispatch', async () => {
  const secret = 'controlled-native-contract-secret-1234567890';
  const env = {
    CUBEJS_API_SECRET: secret,
    SEMANTIC_H3_ENABLED: '1',
    SEMANTIC_H3_SOURCE_VERSION: 'controlled-v1'
  };
  const settings = {
    source: 'snowflake',
    cutoff: Date.now() + 60000
  };
  let calls = 0;
  const driver = {
    query: async sql => {
      calls++;
      return sql.includes('H3_LATLNG') ? cells : coverage;
    }
  };
  const app = express();
  app.use(express.json({
    limit: '128kb'
  }));
  app.post('/cubejs-api/v1/h3', handler(settings, driver, env));
  const server = app.listen(0, '127.0.0.1');
  await new Promise(r => server.once('listening', r));
  const url = `http://127.0.0.1:${server.address().port}/cubejs-api/v1/h3`;
  const authorization = jwt.sign({
    sub: 'sponsoratlas-site'
  }, secret, {
    expiresIn: '1m'
  });
  const request = (body, headers = {}) => fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      authorization,
      'X-SponsorAtlas-Data-Source': 'snowflake',
      'X-SponsorAtlas-Data-Version': 'controlled-v1',
      ...headers
    },
    body: JSON.stringify(body)
  });
  try {
    assert.equal((await request({
      resolution: 3
    }, {
      authorization: 'bad'
    })).status, 403);
    assert.equal((await request({
      resolution: 3
    }, {
      'X-SponsorAtlas-Data-Version': 'other'
    })).status, 409);
    assert.equal((await request({
      resolution: 3,
      sql: 'SELECT private'
    })).status, 400);
    assert.equal((await request({
      resolution: NaN
    })).status, 400);
    assert.equal(calls, 0);
    const result = await request({
      resolution: 3,
      year: 2025,
      employer: 'A'
    });
    assert.equal(result.status, 200);
    const data = await result.json();
    assert.deepEqual(data.identity, {
      source: 'snowflake',
      version: 'controlled-v1'
    });
    assert.equal(data.coverage.total, 11);
    assert.equal(calls, 2);
    settings.cutoff = 0;
    assert.equal((await request({
      resolution: 3
    })).status, 503);
    assert.equal(calls, 2);
  } finally {
    await new Promise(r => server.close(r));
  }
});
test('aborted native query cancels the owned pending statement and prevents second query', async () => {
  const controller = new AbortController();
  let cancelled = 0,
    calls = 0;
  const driver = {
    query: () => {
      calls++;
      const p = new Promise(() => {});
      p.cancel = async () => {
        cancelled++;
      };
      return p;
    }
  };
  const pending = nativeAggregate(driver, {
    resolution: 3
  }, controller.signal);
  controller.abort();
  await assert.rejects(pending, /deadline/);
  assert.equal(cancelled, 1);
  assert.equal(calls, 1);
});
test('native named endpoint admits only one active aggregation', async () => {
  const {
    EventEmitter
  } = require('node:events');
  const secret = 'controlled-concurrency-secret-123456789';
  const env = {
    CUBEJS_API_SECRET: secret,
    SEMANTIC_H3_ENABLED: '1',
    SEMANTIC_H3_SOURCE_VERSION: 'v1'
  };
  let release;
  const api = handler({
    source: 'snowflake',
    cutoff: Date.now() + 60000
  }, {
    query: sql => sql.includes('H3_LATLNG') ? new Promise(r => {
      release = r;
    }) : Promise.resolve(coverage)
  }, env);
  const req = {
    headers: {
      authorization: jwt.sign({
        sub: 'sponsoratlas-site'
      }, secret, {
        'expiresIn': '1m'
      }),
      'x-sponsoratlas-data-source': 'snowflake',
      'x-sponsoratlas-data-version': 'v1'
    },
    body: {
      resolution: 3
    }
  };
  function response() {
    return Object.assign(new EventEmitter(), {
      code: 200,
      writableEnded: false,
      status(code) {
        this.code = code;
        return this;
      },
      json(body) {
        this.body = body;
        this.writableEnded = true;
        return this;
      }
    });
  }
  const first = response(),
    second = response();
  const pending = api(req, first);
  await api(req, second);
  assert.equal(second.code, 429);
  release(cells);
  await pending;
  assert.equal(first.code, 200);
});
