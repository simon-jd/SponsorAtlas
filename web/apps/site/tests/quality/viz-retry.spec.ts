import {test, expect} from '@playwright/test';

test('smoke: Apply retries an unchanged failed visualization scope', async ({page}) => {
  for (const locale of ['en', 'zh']) {
    const calls: URLSearchParams[] = [];
    let fail = true;
    await page.route('**/api/viz?*', async route => {
      calls.push(new URL(route.request().url()).searchParams);
      await route.fulfill({
        status: fail ? 503 : 200,
        contentType: 'application/json',
        body: JSON.stringify({
          cells: [], resolution: 3, histogram: [],
          coverage: {
            total: 1, mapped: 0, unmapped: 1, nullWages: 0,
            history: {SYNTHETIC_TEST_ONLY: 1},
            vintages: {SYNTHETIC_TEST_ONLY: 1},
            mapping: {SYNTHETIC_TEST_ONLY: 1}
          },
          provenance: {kind: 'SYNTHETIC_TEST_ONLY', projection: null, version: 'retry-fixture-v1'}
        })
      });
    });
    const apply = page.getByRole('button', {name: locale === 'zh' ? '应用范围' : 'Apply scope', exact: true});
    const year = page.getByRole('combobox', {name: locale === 'zh' ? '财年' : 'Fiscal year', exact: true});
    const employer = page.getByLabel(locale === 'zh' ? '雇主标识（可选）' : 'Employer identifier (optional)', {exact: true});
    const coverage = page.getByTestId('viz-coverage');
    const alert = page.getByRole('alert').filter({hasText: locale === 'zh'
      ? '可视化数据暂不可用，请检查范围或稍后重试。'
      : 'Visualization unavailable. Check the scope or retry later.'});

    await page.goto(`/${locale}/map`);
    await expect(alert).toBeVisible();
    expect(calls).toHaveLength(1);
    expect([...calls[0].entries()]).toEqual([['resolution', '3']]);

    fail = false;
    await apply.click();
    await expect.poll(() => calls.length, {timeout: 2000}).toBe(2);
    expect(Object.fromEntries(calls[1])).toEqual(Object.fromEntries(calls[0]));
    await expect(coverage).toBeVisible();
    await expect(coverage).toHaveAttribute('aria-busy', 'false');
    await expect(alert).toHaveCount(0);
    expect(calls).toHaveLength(2);

    await year.selectOption('2025');
    fail = true;
    await apply.click();
    await expect(alert).toBeVisible();
    expect(calls).toHaveLength(3);
    expect(Object.fromEntries(calls[2])).toEqual({resolution: '3', year: '2025'});
    expect(calls[2].has('employer')).toBe(false);

    fail = false;
    await apply.click();
    await expect.poll(() => calls.length, {timeout: 2000}).toBe(4);
    expect(Object.fromEntries(calls[3])).toEqual(Object.fromEntries(calls[2]));
    await expect(coverage).toBeVisible();
    await expect(coverage).toHaveAttribute('aria-busy', 'false');
    await expect(alert).toHaveCount(0);
    await expect(year).toHaveValue('2025');
    expect(calls).toHaveLength(4);

    await employer.fill('SYNTHETIC_A');
    await apply.click();
    await expect.poll(() => calls.length, {timeout: 2000}).toBe(5);
    await expect(coverage).toBeVisible();
    await expect(coverage).toHaveAttribute('aria-busy', 'false');
    await expect(alert).toHaveCount(0);
    expect(calls).toHaveLength(5);
    expect(Object.fromEntries(calls[4])).toEqual({resolution: '3', year: '2025', employer: 'SYNTHETIC_A'});
    await page.unroute('**/api/viz?*');
  }
});
