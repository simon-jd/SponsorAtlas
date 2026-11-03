import { expect, test, type Request, type Response } from '@playwright/test';

test('smoke: standalone fixture entry serves matched projection and client assets', async ({ page }) => {
  for (const { locale, heading, banner } of [
    { locale: 'en', heading: 'Application geography and wages', banner: 'SYNTHETIC TEST DATA — not real employer evidence' },
    { locale: 'zh', heading: '申请地点与工资', banner: '合成测试数据 — 不是真实雇主证据' },
  ]) {
    const scripts: Response[] = [];
    const failedScripts: Request[] = [];
    const onResponse = (response: Response) => {
      if (response.request().resourceType() === 'script') scripts.push(response);
    };
    const onRequestFailed = (request: Request) => {
      if (request.resourceType() === 'script') failedScripts.push(request);
    };
    page.on('response', onResponse);
    page.on('requestfailed', onRequestFailed);
    try {
      const vizResponse = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === '/api/viz' && url.search === '?resolution=3';
      });
      const document = await page.goto(`/${locale}/map`);
      expect(document?.status()).toBe(200);
      const viz = await vizResponse;
      expect(viz.status()).toBe(200);
      const data = await viz.json();
      expect(data.coverage).toMatchObject({ total: 37, mapped: 32, unmapped: 5, nullWages: 5 });
      expect(data.provenance).toMatchObject({
        kind: 'CONTROLLED_COORDINATE_FIXTURE',
        source: 'fixture',
        version: 'quality-fixture-v1',
      });

      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
      await expect(page.locator('.test-banner')).toHaveText(banner);
      await expect(page.getByTestId('viz-coverage')).toContainText('37');
      await page.waitForLoadState('networkidle');
      expect(scripts.length).toBeGreaterThan(0);
      expect(failedScripts.map(request => request.url())).toEqual([]);
      for (const response of scripts) {
        expect(response.status(), response.url()).toBe(200);
        expect((await response.body()).length, response.url()).toBeGreaterThan(0);
      }
    } finally {
      page.off('response', onResponse);
      page.off('requestfailed', onRequestFailed);
    }
  }
});
