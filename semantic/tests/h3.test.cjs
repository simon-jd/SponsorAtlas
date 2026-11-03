const {
  test
} = require('node:test');
const assert = require('node:assert/strict');
const {
  scope,
  nativeSql,
  validateQuery
} = require('../h3.cjs');
test('native H3 validates scope before Snowflake wrapping; fixed public SQL has exact median', () => {
  for (const value of ['16', 'NaN', '2.5', '-1']) assert.throws(() => scope({
    SEMANTIC_H3_RESOLUTION: value
  }));
  assert.throws(() => scope({
    SEMANTIC_H3_EMPLOYER: "a' OR 1=1"
  }));
  const sql = nativeSql({
    resolution: 3,
    year: 2025,
    employer: 'A'
  });
  assert.match(sql, /H3_INT_TO_STRING\(H3_LATLNG_TO_CELL\(CASE WHEN/);
  assert.match(sql, /MEDIAN\(f.annual_wage\)/);
  assert.match(sql, /f.fiscal_year=2025/);
  assert.match(sql, /f.employer_id='A'/);
  assert.doesNotMatch(sql, /RAW|STAGING|FACT_MY_APPLICATIONS/);
});
test('optional model only accepts complete fixed scope and cannot combine medians', () => {
  const q = {
    dimensions: ['h3.h3', 'h3.count', 'h3.wage_p50', 'h3.resolution'],
    limit: 10000
  };
  assert.equal(validateQuery(q, true), true);
  assert.throws(() => validateQuery(q, false));
  assert.throws(() => validateQuery({
    ...q,
    measures: ['h3.wage_p50']
  }, true));
  assert.throws(() => validateQuery({
    ...q,
    filters: [{
      member: 'h3.resolution',
      operator: 'equals',
      values: ['5']
    }]
  }, true));
  assert.equal(validateQuery({
    measures: ['lca.case_count']
  }, false), false);
});
test('default repository excludes H3; opt-in Snowflake rewrites it canonically without breaking original projections', async () => {
  const {
    modelRepository,
    assertReadQuery
  } = require('../source.cjs');
  delete process.env.SEMANTIC_H3_ENABLED;
  const normal = await modelRepository('snowflake', {
    revision: 'a'.repeat(32)
  }).dataSchemaFiles();
  assert.ok(!normal.some(f => /h3\.yml$/.test(f.fileName)));
  assert.equal(normal.filter(f => /cubes[/\\]/.test(f.fileName)).length, 3);
  process.env.SEMANTIC_H3_ENABLED = '1';
  try {
    const optional = await modelRepository('snowflake', {
      revision: 'a'.repeat(32)
    }).dataSchemaFiles();
    const model = optional.find(f => /h3\.yml$/.test(f.fileName));
    assert.match(model.content, /H3_LATLNG_TO_CELL/);
    assert.equal(optional.filter(f => /cubes[/\\]/.test(f.fileName)).length, 4);
    assertReadQuery(nativeSql());
  } finally {
    delete process.env.SEMANTIC_H3_ENABLED;
  }
});
test('invalid H3 projections are client errors in the installed Cube formatter', () => {
  const { ApiGateway } = require('../node_modules/@cubejs-backend/api-gateway/dist/src/gateway.js');
  const complete = {
    dimensions: ['h3.h3', 'h3.count', 'h3.wage_p50', 'h3.resolution'],
    limit: 10000
  };
  const format = error => {
    let result;
    ApiGateway.prototype.handleError.call({
      devServer: false,
      sanitizeQueryForLogging: query => query,
      log: () => {},
      duration: () => 0
    }, {
      e: error,
      context: {},
      query: {},
      redactedQuery: null,
      requestStarted: 0,
      res: (body, options) => { result = { body, status: options.status }; }
    });
    return result;
  };
  const invalid = [
    { query: structuredClone(complete), enabled: false },
    { query: { ...complete, dimensions: ['h3.h3'] }, enabled: true },
    { query: { ...complete, measures: ['h3.wage_p50'] }, enabled: true },
    { query: { ...complete, filters: [{ member: 'h3.resolution', operator: 'equals', values: ['5'] }] }, enabled: true },
    { query: { ...complete, order: { 'h3.unknown': 'asc' } }, enabled: true }
  ];
  for (const { query, enabled } of invalid) {
    let error;
    try {
      validateQuery(structuredClone(query), enabled);
    } catch (caught) {
      error = caught;
    }
    assert.ok(error, 'invalid H3 query must throw');
    const response = format(error);
    assert.equal(response.status, 400);
    assert.equal(response.body.type, 'UserError');
  }
  const valid = structuredClone(complete);
  assert.equal(validateQuery(valid, true), true);
  assert.equal(valid.limit, 10000);
  assert.equal(validateQuery({ measures: ['lca.case_count'] }, false), false);
  assert.equal(format(Error('unrelated internal failure')).status, 500);
});
