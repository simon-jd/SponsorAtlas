import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { visualization } from '../lib/viz-source.ts';
test('server source verifies snapshot identity/integrity and invalidates aggregation cache across revisions', () => {
  const cwd = process.cwd();
  const saved = {
    ...process.env
  };
  const temp = mkdtempSync(resolve(process.env.TMPDIR || tmpdir(), 'viz-source-'));
  const site = resolve(temp, 'web/apps/site');
  const dir = resolve(temp, '.tools/viz/fixture');
  const write = (version: string, wage: number) => {
    const row = {
      employer: 'A',
      fiscal_year: 2025,
      employer_history_status: 'CONTROLLED',
      latitude: 37.7,
      longitude: -122.4,
      county_reference_vintage: 'CONTROLLED',
      coordinate_reference_vintage: 'CONTROLLED',
      coordinate_semantics: 'CONTROLLED_COORDINATE_FIXTURE',
      county_mapping_status: 'CONTROLLED',
      annual_wage: wage,
      count: 1
    };
    const bytes = JSON.stringify({
      columns: Object.keys(row),
      rows: [Object.values(row)]
    });
    const file = resolve(dir, 'frequencies.json');
    rmSync(file, {
      force: true
    });
    writeFileSync(file, bytes, {
      mode: 0o444
    });
    rmSync(resolve(dir,'manifest.json'),{force:true});
    writeFileSync(resolve(dir, 'manifest.json'), JSON.stringify({
      kind: 'CONTROLLED_COORDINATE_FIXTURE',
      sourceSnapshot: version,
      bytes: Buffer.byteLength(bytes),
      sha256: createHash('sha256').update(bytes).digest('hex')
    }),{mode:0o444});
  };
  try {
    mkdirSync(site, { recursive: true });
    process.chdir(site);
    mkdirSync(dir, {
      recursive: true
    });
    process.env.SITE_DATA_SOURCE = 'fixture';
    process.env.SITE_SOURCE_VERSION = 'v1';
    process.env.SITE_PACKAGED_PROJECTION='1';
    assert.throws(()=>visualization({resolution:3}),/Packaged projection/);
    process.env.SITE_DATA_SOURCE='parquet';process.env.SITE_SNAPSHOT_SHA='a'.repeat(64);process.env.SITE_VIZ_DIRECTORY=dir;
    assert.throws(()=>visualization({resolution:3}),/Packaged projection/);
    delete process.env.SITE_PACKAGED_PROJECTION;delete process.env.SITE_VIZ_DIRECTORY;process.env.SITE_DATA_SOURCE='fixture';
    write('v1', 10);
    assert.equal(visualization({
      resolution: 3
    }).cells[0].wage_p50, 10);
    process.env.SITE_SOURCE_VERSION = 'v2';
    assert.throws(() => visualization({
      resolution: 3
    }), /identity mismatch/);
    write('v2', 20);
    assert.equal(visualization({
      resolution: 3
    }).cells[0].wage_p50, 20);
    process.env.SITE_DATA_SOURCE = 'parquet';
    process.env.SITE_SNAPSHOT_SHA = 'a'.repeat(64);
    process.env.SITE_VIZ_DIRECTORY = dir;
    assert.throws(() => visualization({
      resolution: 3
    }), /identity mismatch/);
    delete process.env.SITE_VIZ_DIRECTORY;
    process.env.SITE_DATA_SOURCE = 'fixture';
    chmodSync(resolve(dir, 'frequencies.json'), 0o644);
    writeFileSync(resolve(dir, 'frequencies.json'), '[]');
    process.env.SITE_SOURCE_VERSION = 'v3';
    write('v3', 30);
    chmodSync(resolve(dir, 'frequencies.json'), 0o644);
    assert.throws(() => visualization({
      resolution: 3
    }), /artifact/);
  } finally {
    process.chdir(cwd);
    process.env = saved;
    rmSync(temp, {
      recursive: true,
      force: true
    });
  }
});
