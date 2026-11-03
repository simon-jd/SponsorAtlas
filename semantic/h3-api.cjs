// One named native aggregation endpoint, authenticated independently of Cube's loader.
const jwt = require('jsonwebtoken');
const {
  scope,
  nativeAggregate
} = require('./h3.cjs');
function handler(settings, driver, env = process.env) {
  let active = false;
  return async (req, res) => {
    if (settings.source !== 'snowflake' || env.SEMANTIC_H3_ENABLED !== '1' || Date.now() >= settings.cutoff) return res.status(503).json({
      error: 'Native H3 unavailable'
    });
    try {
      const token = jwt.verify(req.headers.authorization, env.CUBEJS_API_SECRET, {
        algorithms: ['HS256']
      });
      if (token.sub !== 'sponsoratlas-site') throw Error();
    } catch {
      return res.status(403).json({
        error: 'Authentication required'
      });
    }
    if (!/^[A-Za-z0-9._:-]{1,100}$/.test(env.SEMANTIC_H3_SOURCE_VERSION || '') || req.headers['x-sponsoratlas-data-source'] !== 'snowflake' || req.headers['x-sponsoratlas-data-version'] !== env.SEMANTIC_H3_SOURCE_VERSION) return res.status(409).json({
      error: 'Source identity mismatch'
    });
    let selected;
    try {
      const body = req.body;
      if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some(k => !['resolution', 'year', 'employer'].includes(k)) || !Number.isInteger(body.resolution) || body.year !== undefined && !Number.isInteger(body.year) || body.employer !== undefined && (typeof body.employer !== 'string'||body.employer.length===0)) throw Error();
      selected = scope({
        SEMANTIC_H3_RESOLUTION: String(body.resolution),
        ...(body.year === undefined ? {} : {
          SEMANTIC_H3_YEAR: String(body.year)
        }),
        ...(body.employer === undefined ? {} : {
          SEMANTIC_H3_EMPLOYER: body.employer
        })
      });
    } catch {
      return res.status(400).json({
        error: 'Invalid aggregate scope'
      });
    }
    if (active) return res.status(429).json({
      error: 'Native aggregate busy'
    });
    active = true;
    const controller = new AbortController();
    const closed = () => {
      if (!res.writableEnded) controller.abort();
    };
    res.once('close', closed);
    try {
      const result = await nativeAggregate(driver, selected, controller.signal);
      return res.json({
        ...result,
        identity: {
          source: 'snowflake',
          version: env.SEMANTIC_H3_SOURCE_VERSION
        },
        kind: 'COUNTY_INTERNAL_POINT_NOT_WORKSITE'
      });
    } catch {
      return res.status(503).json({
        error: 'Native H3 query unavailable'
      });
    } finally {
      active = false;
      res.removeListener('close', closed);
    }
  };
}
module.exports = {
  handler
};
