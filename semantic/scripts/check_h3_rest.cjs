// Focused real Cube compile/REST check, reuses immutable exports without preparing sources.
const {
  spawn
} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const secret = crypto.randomBytes(40).toString('hex');
const jwt = require('jsonwebtoken').sign({
  sub: 'h3-local-proof'
}, secret, {
  expiresIn: '5m'
});
const pause = ms => new Promise(r => setTimeout(r, ms));
async function phase(enabled) {
  const child = spawn('bash', [path.join(root, 'semantic/run.sh'), 'serve', 'parquet', 'actual'], {
    stdio: 'inherit',
    env: {
      ...process.env,
      CUBEJS_API_SECRET: secret,
      PORT: '4100',
      SEMANTIC_H3_ENABLED: enabled ? '1' : '0',
      SEMANTIC_H3_WORK: path.join(root, '.tools/viz/actual')
    }
  });
  try {
    let response;
    for (let i = 0; i < 150; i++) {
      try {
        response = await fetch('http://127.0.0.1:4100/cubejs-api/v1/meta', {
          headers: {
            Authorization: jwt
          }
        });
        if (response.ok) break;
      } catch {}
      if (child.exitCode !== null) throw Error('Cube exited');
      await pause(100);
    }
    if (!response?.ok) throw Error('Cube readiness');
    const meta = await response.json();
    const nativeGate=await fetch('http://127.0.0.1:4100/cubejs-api/v1/h3',{method:'POST',headers:{Authorization:jwt,'Content-Type':'application/json'},body:JSON.stringify({resolution:3})});if(nativeGate.status!==503)throw Error('Native-only endpoint guard/wiring');
    const found = meta.cubes.some(c => c.name === 'h3');
    if (found !== enabled) throw Error('Optional surface leak');
    const query = {
      dimensions: ['h3.h3', 'h3.count', 'h3.wage_p50', 'h3.resolution'],
      limit: 10000
    };
    const result = await fetch('http://127.0.0.1:4100/cubejs-api/v1/load', {
      method: 'POST',
      headers: {
        Authorization: jwt,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        query
      })
    });
    const body = await result.json();
    if (enabled) {
      if (!result.ok || body.error) throw Error(JSON.stringify(body));
      const m = JSON.parse(fs.readFileSync(path.join(root, '.tools/viz/actual/h3-manifest.json')));
      if (body.data.length !== m.rows || body.data.reduce((n, r) => n + Number(r['h3.count']), 0) !== m.coverage.mapped) throw Error('REST aggregate reconciliation');
      if (body.data.some(r => typeof r['h3.h3'] !== 'string' || Number(r['h3.resolution']) !== 3)) throw Error('REST representation');
      return {
        enabled,
        status: result.status,
        cells: body.data.length,
        mapped: m.coverage.mapped,
        models: meta.cubes.map(c => c.name)
      };
    }
    if (result.ok && !body.error) throw Error('Disabled H3 unexpectedly queryable');
    return {
      enabled,
      status: result.status,
      models: meta.cubes.map(c => c.name)
    };
  } finally {
    child.kill('SIGTERM');
    for (let i = 0; i < 100 && child.exitCode === null; i++) await pause(50);
    if (child.exitCode === null) {
      child.kill('SIGKILL');
      await new Promise(r => child.once('exit', r));
    }
  }
}
(async () => {
  const results = [await phase(false), await phase(true)];
  console.log(JSON.stringify(results, null, 2));
  fs.writeFileSync(path.join(root, 'web/apps/site/evidence/viz-cube-rest.json'), JSON.stringify(results, null, 2) + '\n');
})().catch(e => {
  console.error(e);
  process.exitCode = 1;
});
