// Optional fixed-scope semantic surface. Never combines medians from cells/scopes.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const MEMBERS = ['h3.h3', 'h3.count', 'h3.wage_p50', 'h3.resolution'];
function scope(env = process.env) {
  const resolution = Number(env.SEMANTIC_H3_RESOLUTION ?? 3);
  if (!Number.isInteger(resolution) || resolution < 0 || resolution > 7) throw Error('Invalid H3 resolution');
  const year = env.SEMANTIC_H3_YEAR ? Number(env.SEMANTIC_H3_YEAR) : null;
  if (year !== null && (!Number.isInteger(year) || year < 1900 || year > 2200)) throw Error('Invalid H3 year');
  const employer = env.SEMANTIC_H3_EMPLOYER || null;
  if (employer !== null && (!/^[A-Za-z0-9_-]{1,80}$/.test(employer) || employer === 'UNKNOWN')) throw Error('Invalid H3 employer');
  return {
    resolution,
    year,
    employer
  };
}
function nativeSql(s = scope()) {
  s = scope({
    SEMANTIC_H3_RESOLUTION: String(s.resolution),
    ...(s.year !== null && s.year !== undefined ? {
      SEMANTIC_H3_YEAR: String(s.year)
    } : {}),
    ...(s.employer ? {
      SEMANTIC_H3_EMPLOYER: s.employer
    } : {})
  });
  const valid = 'g.county_internal_latitude BETWEEN -90 AND 90 AND g.county_internal_longitude BETWEEN -180 AND 180';
  return `SELECT H3_INT_TO_STRING(H3_LATLNG_TO_CELL(CASE WHEN ${valid} THEN g.county_internal_latitude END, CASE WHEN ${valid} THEN g.county_internal_longitude END, ${s.resolution})) AS h3, COUNT(*) AS count, MEDIAN(f.annual_wage) AS wage_p50, ${s.resolution} AS resolution FROM ANALYTICS.MARTS.FACT_LCA_CASE f LEFT JOIN ANALYTICS.MARTS.DIM_GEOGRAPHY g ON f.geography_key=g.geography_key WHERE ${valid}${s.year ? ` AND f.fiscal_year=${s.year}` : ''}${s.employer ? ` AND f.employer_id='${s.employer}'` : ''} GROUP BY 1`;
}
function validateQuery(query, enabled) {
  const all = [...(query.measures || []), ...(query.dimensions || [])];
  if (!all.some(m => m.startsWith('h3.'))) return false;
  if (!enabled || query.measures?.length || JSON.stringify([...(query.dimensions || [])].sort()) !== JSON.stringify([...MEMBERS].sort()) || query.filters?.length || query.timeDimensions?.length || query.segments?.length || query.offset || query.limit > 10000) throw Object.assign(new Error('H3 requires the complete fixed-scope aggregate projection'), { type: 'UserError' });
  if (query.order && Object.keys(query.order).some(m => !MEMBERS.includes(m))) throw Object.assign(new Error('Invalid H3 order'), { type: 'UserError' });
  query.limit = 10000;
  return true;
}
function optionalSnapshot(env = process.env) {
  if (env.SEMANTIC_H3_ENABLED !== '1') return null;
  if (!env.SEMANTIC_H3_WORK) throw Error('Dedicated H3 snapshot required');
  const dir = path.resolve(env.SEMANTIC_H3_WORK),
    file = path.join(dir, 'h3.parquet');
  const manifestFile=path.join(dir,'h3-manifest.json');const manifestStat=fs.statSync(manifestFile);
  if(manifestStat.size>10000||(manifestStat.mode&0o222)!==0)throw Error('Immutable H3 manifest required');
  const m = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  const st = fs.statSync(file);
  if (st.mode & 0o222 || st.size > 5000000 || st.size !== m.bytes || m.kind !== 'COUNTY_INTERNAL_POINT_NOT_WORKSITE' || m.rows > 10000 || JSON.stringify(m.scope) !== JSON.stringify(scope(env))) throw Error('Invalid fixed H3 snapshot');
  if (crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== m.sha256) throw Error('H3 snapshot integrity mismatch');
  const source = JSON.parse(fs.readFileSync(path.join(require('./snapshot.cjs').snapshotDirectory(env), 'export-evidence.json'), 'utf8')).exports.find(e => e.path.endsWith('/lca.parquet'));
  if (!source || m.sourceSnapshot !== source.sha256) throw Error('H3 source identity mismatch');
  return {
    file,
    manifest: m
  };
}
module.exports = {
  scope,
  nativeSql,
  validateQuery,
  optionalSnapshot
};
function nativeCoverageSql(s) {
  // nativeSql validates the exact same scope before interpolation.
  nativeSql(s);
  const valid = 'g.county_internal_latitude BETWEEN -90 AND 90 AND g.county_internal_longitude BETWEEN -180 AND 180';
  return `SELECT f.employer_history_status AS history, g.county_reference_vintage AS county_vintage, g.coordinate_reference_vintage AS coordinate_vintage, f.county_mapping_status AS mapping, CASE WHEN f.annual_wage IS NULL THEN -1 WHEN f.annual_wage<0 THEN -2 ELSE LEAST(10,FLOOR(f.annual_wage/50000)) END AS wage_band, CASE WHEN ${valid} THEN 1 ELSE 0 END AS mapped, COUNT(*) AS count FROM ANALYTICS.MARTS.FACT_LCA_CASE f LEFT JOIN ANALYTICS.MARTS.DIM_GEOGRAPHY g ON f.geography_key=g.geography_key${s.year || s.employer ? ` WHERE ${[s.year ? `f.fiscal_year=${s.year}` : null, s.employer ? `f.employer_id='${s.employer}'` : null].filter(Boolean).join(' AND ')}` : ''} GROUP BY 1,2,3,4,5,6`;
}
async function nativeAggregate(driver, s, signal) {
  const query = async sql => {
    signal?.throwIfAborted();
    const pending = driver.query(sql, []);
    let timer, abort, cancellation;
    const cancelled = new Promise((_, reject) => {
      abort = () => {
        cancellation ??= Promise.resolve(pending.cancel?.());
        cancellation.then(() => reject(Error('Native query deadline')), () => reject(Error('Native cancellation failed')));
      };
      timer = setTimeout(abort, 6500);
      signal?.addEventListener('abort', abort, {
        once: true
      });
    });
    try {
      return await Promise.race([pending, cancelled]);
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      if (cancellation) await cancellation.catch(() => {});
    }
  };
  const cellRows = await query(nativeSql(s) + ' LIMIT 10001');
  const coverageRows = await query(nativeCoverageSql(s) + ' LIMIT 10001');
  if (cellRows.length > 10000 || coverageRows.length > 10000) throw Error('Native aggregate bound exceeded');
  const field = (row, key) => Object.hasOwn(row, key) ? row[key] : row[key.toUpperCase()];
  const number = (value, integer = false) => {
    if (typeof value !== 'number' && typeof value !== 'string' || value === '') throw Error('Invalid native number');
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0 || integer && !Number.isSafeInteger(n)) throw Error('Invalid native number');
    return n;
  };
  const cells = cellRows.map(row => {
    const h3 = field(row, 'h3');
    if (typeof h3 !== 'string' || !/^8[0-9a-f]{14}$/.test(h3) || Number(BigInt('0x' + h3) >> 52n & 15n) !== s.resolution) throw Error('Invalid native H3');
    const wage = field(row, 'wage_p50');
    return {
      h3,
      count: number(field(row, 'count'), true),
      wage_p50: wage === null ? null : number(wage)
    };
  });
  if (new Set(cells.map(c => c.h3)).size !== cells.length) throw Error('Duplicate native cell');
  const coverage = {
    total: 0,
    mapped: 0,
    unmapped: 0,
    nullWages: 0,
    history: {},
    vintages: {},
    mapping: {}
  };
  const histogram = Array.from({
    length: 11
  }, (_, i) => ({
    lower: i * 50000,
    upper: i === 10 ? null : (i + 1) * 50000,
    count: 0
  }));
  const code = value => {
    if (value === null) return 'UNKNOWN';
    if (typeof value !== 'string' || value.length > 100) throw Error('Invalid native code');
    return value;
  };
  const add = (group, key, count) => Object.defineProperty(group, key, {
    value: (Object.hasOwn(group, key) ? group[key] : 0) + count,
    enumerable: true,
    configurable: true,
    writable: true
  });
  for (const row of coverageRows) {
    const count = number(field(row, 'count'), true),
      mapped = number(field(row, 'mapped'), true),
      band = Number(field(row, 'wage_band'));
    if (![0, 1].includes(mapped) || !Number.isInteger(band) || band < -1 || band > 10) throw Error('Invalid coverage bin');
    coverage.total += count;
    coverage[mapped ? 'mapped' : 'unmapped'] += count;
    if (band === -1) coverage.nullWages += count;else histogram[band].count += count;
    add(coverage.history, code(field(row, 'history')), count);
    add(coverage.vintages, `${code(field(row, 'county_vintage'))} → ${code(field(row, 'coordinate_vintage'))}`, count);
    add(coverage.mapping, code(field(row, 'mapping')), count);
  }
  if (!Number.isSafeInteger(coverage.total) || cells.reduce((n, c) => n + c.count, 0) !== coverage.mapped) throw Error('Native count preservation failed');
  const result = {
    cells,
    histogram,
    coverage,
    resolution: s.resolution
  };
  if (Buffer.byteLength(JSON.stringify(result)) > 2000000) throw Error('Native response too large');
  return result;
}
module.exports.nativeCoverageSql = nativeCoverageSql;
module.exports.nativeAggregate = nativeAggregate;
