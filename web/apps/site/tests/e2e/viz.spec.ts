import { test, expect, chromium } from '@playwright/test';
import {evaluateFrameIntervals,evaluateFrameWork} from '../../lib/viz-frame.ts';
import AxeBuilder from '@axe-core/playwright';
import { gzipSync } from 'node:zlib';
import { readFileSync, writeFileSync } from 'node:fs';
for (const locale of ['en', 'zh']) test(`${locale} aggregate fallback, keyboard, dark and a11y`, async ({
  page
}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...args: unknown[]) {
      if (kind.startsWith('webgl')) return null;
      return original.apply(this, [kind, ...args] as never);
    } as typeof original;
  });
  await page.goto(`/${locale}/map`);
  await expect(page.getByTestId('viz-coverage')).toBeVisible();
  await page.getByTestId('enable-map').click();
  await expect(page.getByTestId('viz-fallback')).toBeVisible();
  await expect(page.getByTestId('viz-cell-table')).toBeVisible();
  await expect(page.getByTestId('viz-static-map')).toBeVisible();
  await expect(page.getByTestId('wage-static-chart')).toBeVisible();
  const table=page.getByTestId('viz-cell-table');await table.focus();await page.keyboard.press('ArrowDown');await expect.poll(()=>table.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);await page.keyboard.press('Home');await expect.poll(()=>table.evaluate(el=>el.scrollTop)).toBe(0);
  await page.locator('[data-theme-toggle]').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect((await new AxeBuilder({
    page
  }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: `evidence/viz-${locale}-fallback-dark.png`,
    fullPage: true
  });
});
test('reduced motion disables all required viz transitions and keyboard operates map', async ({
  page
}) => {
  await page.emulateMedia({
    reducedMotion: 'reduce'
  });
  await page.goto('/en/map');
  await page.getByTestId('enable-map').click();
  await expect(page.getByTestId('viz-scene')).toHaveAttribute('data-motion', 'none');
  await expect(page.getByTestId('viz-scene')).toHaveAttribute('data-drawn', /[1-9]/);
  const zoom = page.getByRole('button', {
    name: 'Zoom in',
    exact: true
  });
  await zoom.focus();
  await page.keyboard.press('Enter');
  await expect(zoom).toBeFocused();
  await expect(page.getByTestId('viz-scene')).toHaveAttribute('data-resolution','4');
  expect(await page.getByTestId('viz-scene').evaluate(el => el.getAnimations({
    subtree: true
  }).filter(a => a.playState === 'running').length)).toBe(0);
  await page.screenshot({
    path: 'evidence/viz-en-reduced-motion.png',
    fullPage: true
  });
});
for (const rate of [1, 4]) test(`drawn WebGL pan zoom p95 at ${rate}x CPU`, async ({
  page,
  context
}) => {
  await page.goto('/en/map');
  await page.getByTestId('enable-map').click();
  const scene = page.getByTestId('viz-scene');
  await expect(scene).toHaveAttribute('data-drawn', /[1-9]/);
  await expect(scene).toHaveAttribute('data-cells', /[1-9]/);
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', {
    rate
  });
  const trace = await page.evaluate(async () => {
    const el = document.querySelector('[data-testid="viz-scene"]') as HTMLElement;
    const layerBefore=Number(el.dataset.layerBuilds);
      const before = Number(el.dataset.drawn),
      intervals: number[] = [], script: number[] = [], render: number[] = [];
    let prev = 0;
    const last = (name: string) => performance.getEntriesByName(name, 'measure').at(-1)?.duration;
    // Full source workload remains mounted. Warm up 60 displayed frames, measure 240. Each
    // frame: one pan/zoom input (viz-script), then wait for deck.gl's draw (viz-render).
    for (let i = 0; i < 300; i++) await new Promise<void>(resolve => requestAnimationFrame(t => {
      if (i >= 60) intervals.push(t - prev);
      prev = t;
      performance.clearMeasures();
      window.dispatchEvent(new CustomEvent('viz-panzoom', {
        detail: {
          longitude: -98 + Math.sin(i / 23) * 22,
          latitude: 38 + Math.cos(i / 31) * 8,
          zoom: 3.5 + Math.sin(i / 39) * .4
        }
      }));
      const s = last('viz-script');
      requestAnimationFrame(() => setTimeout(() => {
        const r = last('viz-render');
        if (i >= 60 && s !== undefined && r !== undefined) { script.push(s); render.push(r); }
        resolve();
      }, 0));
    }));
    return {
      intervals,
      script,
      render,
      before,
      layerBefore,
      layerAfter:Number(el.dataset.layerBuilds),
      after: Number(el.dataset.drawn),
      renderer: el.dataset.renderer,
      vendor: el.dataset.vendor,
      cells: Number(el.dataset.cells),
      resolution: el.dataset.resolution,
      viewport: [innerWidth, innerHeight],
      context: el.dataset.context
    };
  });
  // Gate: per-frame main-thread work (performance.measure), not rAF intervals, which vsync caps.
  const work=evaluateFrameWork(trace.script,trace.render,rate as 1|4);
  const intervalsInfo=evaluateFrameIntervals(trace.intervals,rate as 1|4);
  writeFileSync(`evidence/viz-frame-${process.env.VIZ_EVIDENCE || process.env.VIZ_GPU || 'default'}-${rate}x.json`, JSON.stringify({
    ...trace,
    rate,
    p95: work.p95,
    work,
    rafIntervalsInfoOnly: intervalsInfo,
    definition: work.definition,
    threshold: rate === 1 ? 16.7 : 33,
    software: /swiftshader|llvmpipe|software/i.test(trace.renderer || '')
  }, null, 2));
  expect(trace.after - trace.before).toBeGreaterThan(200);
    expect(trace.layerAfter).toBe(trace.layerBefore);
  expect(trace.context).toBe('webgl2');
  expect(work.samples).toBeGreaterThan(200);
  expect(work.p95Nanoseconds).toBeLessThanOrEqual(work.thresholdNanoseconds);
});
test('aggregate API bounds, no point leakage and deferred first-navigation JavaScript', async ({
  page,
  request
}) => {
  const pending: Promise<Buffer>[] = [];
  page.on('response', r => {
    if (r.url().includes('/_next/') && r.url().split('?')[0].endsWith('.js')) pending.push(r.body());
  });
  await page.goto('/en/map');
  await expect(page.getByTestId('enable-map')).toBeVisible();
  const bytes = (await Promise.all(pending)).reduce((n, b) => n + gzipSync(b).length, 0);
  writeFileSync('evidence/viz-initial-js.json', JSON.stringify({
    gzipBytes: bytes,
    threshold: 200 * 1024,
    deferredGraphics: true
  }));
  expect(bytes).toBeLessThanOrEqual(200 * 1024);
  expect((await request.get('/api/viz?sql=select')).status()).toBe(400);
  expect((await request.get('/api/viz?resolution=8')).status()).toBe(400);
  const data = await (await request.get('/api/viz?resolution=3&year=2025')).json();
  // Independent count from the canonical fact build (594,821 FY2025 cases), not from the map projection.
  const facts = JSON.parse(readFileSync('../../../docs/evidence/task09-backfill/fact-summary.json', 'utf8'));
  expect(data.coverage.total).toBe(facts.lca_by_fiscal_year.find((y: {fiscal_year: number}) => y.fiscal_year === 2025).cases);
  expect(data.cells.every((c: object) => Object.keys(c).sort().join(',') === 'count,h3,wage_p50')).toBe(true);
  expect(JSON.stringify(data)).not.toMatch(/latitude|longitude|case_number/);
  await page.goto('/en/map');
  await page.getByLabel('Employer identifier (optional)').fill('NO_SUCH_EMPLOYER');
  await page.getByRole('button', {
    name: 'Apply scope'
  }).click();
  await expect(page.getByText('No data in this scope.', {
    exact: true
  })).toBeVisible();
});

test('controlled coarse pentagons draw and pointer drag remains interactive',async({page})=>{
 const {getPentagons}=await import('h3-js');const cells=getPentagons(0).map(h3=>({h3,count:1,wage_p50:100}));
 await page.route('**/api/viz?*',route=>route.fulfill({json:{cells,resolution:0,histogram:Array.from({length:11},(_,i)=>({lower:i*50000,upper:i===10?null:(i+1)*50000,count:i===0?12:0})),coverage:{total:12,mapped:12,unmapped:0,nullWages:0,history:{CONTROLLED:12},vintages:{CONTROLLED:12},mapping:{CONTROLLED:12}},provenance:{kind:'CONTROLLED_PENTAGON_FIXTURE',projection:'controlled-pentagon-fixture'}}}));
 await page.goto('/en/map');await page.getByTestId('enable-map').click();const scene=page.getByTestId('viz-scene');await expect(scene).toHaveAttribute('data-drawn',/[1-9]/);await expect(scene).toHaveAttribute('data-cells','12');await expect(scene).toHaveAttribute('data-resolution','0');const box=await page.locator('.hex-map').boundingBox();if(!box)throw Error('No map');await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+80,box.y+box.height/2,{steps:8});await page.mouse.up();await expect(page.getByTestId('viz-fallback')).toHaveCount(0);
});
