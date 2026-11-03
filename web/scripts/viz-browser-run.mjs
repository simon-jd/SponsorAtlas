import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createServer } from 'node:net';
const root = resolve('../../..');
const manifest = JSON.parse(readFileSync(resolve(root, '.tools/viz/worksite-fy2025/manifest.json'), 'utf8'));
const probe = createServer();
await new Promise((ok, no) => {
  probe.once('error', no);
  probe.listen(3100, '127.0.0.1', ok);
});
await new Promise(ok => probe.close(ok));
const children = [];
const pause = ms => new Promise(r => setTimeout(r, ms));
function run(cmd, args, env = {}) {
  const c = spawn(cmd, args, {
    stdio: 'inherit',
    env: {
      ...process.env,
      ...env
    }
  });
  children.push(c);
  return c;
}
try {
  run('pnpm', ['start'], {
    SITE_DATA_SOURCE: 'parquet',
    SITE_SNAPSHOT_SHA: manifest.sourceSnapshot
  });
  for (let i = 0; i < 150; i++) {
    try {
      await fetch('http://127.0.0.1:3100/en/map');
      break;
    } catch {}
    if (children.some(c => c.exitCode !== null)) throw Error('Service exited');
    await pause(100);
  }
  const c = run('pnpm', ['exec', 'playwright', 'test', '--config', 'viz.playwright.config.ts', ...(process.argv[2] ? ['--grep', process.argv[2]] : [])]);
  process.exitCode = await new Promise(ok => c.once('exit', code => ok(code ?? 1)));
} finally {
  for (const c of children.reverse()) if (c.exitCode === null) c.kill('SIGTERM');
  await Promise.all(children.map(async c => {
    for (let i = 0; i < 40 && c.exitCode === null; i++) await pause(50);
    if (c.exitCode === null) c.kill('SIGKILL');
  }));
}
