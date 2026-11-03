import { test, expect, type Locator } from "@playwright/test";

const id1 = "emp_" + "1".repeat(64);
const id2 = "emp_" + "2".repeat(64);
const control = "http://127.0.0.1:4100";

async function expectLinkContext(
  link: Locator,
  pathname: string,
  params: Record<string, string>,
) {
  const href = await link.getAttribute("href");
  expect(href).not.toBeNull();
  const url = new URL(href!, "http://127.0.0.1:3100");
  expect(url.pathname).toBe(pathname);
  expect(Object.fromEntries(url.searchParams)).toEqual(params);
}

test("smoke: search and employer retries retain context and recover in both locales", async ({
  page,
  request,
}) => {
  try {
    // Readiness primes only the empty search. Each failed query and detail is new.
    for (const { locale, target, selected, retry } of [
      { locale: "en", target: id2, selected: id1, retry: "Retry" },
      { locale: "zh", target: id1, selected: id2, retry: "重试" },
    ]) {
      const search = `/${locale}?q=${target}&ids=${selected}`;
      const detail = `/${locale}/employers/${target}?ids=${selected}`;
      await expect((await request.get(`${control}/test-fail`)).status()).toBe(200);
      await page.goto(search);
      const searchAlert = page.locator("p[role=alert]");
      await expect(searchAlert).toBeVisible();
      const searchRetry = searchAlert.getByRole("link", { name: retry, exact: true });
      await expectLinkContext(searchRetry, `/${locale}`, { q: target, ids: selected });
      await expect((await request.get(`${control}/test-ok`)).status()).toBe(200);
      await searchRetry.click();
      await expect(page.locator("p[role=alert]")).toHaveCount(0);
      await expectLinkContext(
        page.locator(`[data-employer-link="${target}"]`),
        `/${locale}/employers/${target}`,
        { ids: selected },
      );

      await expect((await request.get(`${control}/test-fail`)).status()).toBe(200);
      await page.goto(detail);
      const detailAlert = page.locator("p[role=alert]");
      await expect(detailAlert).toBeVisible();
      const detailRetry = detailAlert.getByRole("link", { name: retry, exact: true });
      await expectLinkContext(detailRetry, `/${locale}/employers/${target}`, { ids: selected });
      await expect((await request.get(`${control}/test-ok`)).status()).toBe(200);
      await detailRetry.click();
      await expect(page.locator("p[role=alert]")).toHaveCount(0);
      await expect(page.locator("table")).toContainText(target);
      await expectLinkContext(
        page.locator("[data-add-compare]"),
        `/${locale}/compare`,
        { ids: `${selected},${target}` },
      );
    }
  } finally {
    await request.get(`${control}/test-ok`);
  }
});

test("smoke: missing history is explicit in detail and compare, while known codes remain intact", async ({
  page,
}) => {
  for (const { locale, missing } of [
    { locale: "en", missing: "History status not recorded" },
    { locale: "zh", missing: "历史状态未记录" },
  ]) {
    await page.goto(`/${locale}/employers/${id2}`);
    await expect(page.locator("tbody tr").filter({ hasText: id2 })).toContainText(missing);
    await page.goto(`/${locale}/employers/${id1}`);
    await expect(page.locator("tbody tr").filter({ hasText: id1 })).toContainText("UNCERTAIN");
    await page.goto(`/${locale}/compare?ids=${id2},${id1}`);
    const rows = page.locator("tbody tr");
    await expect(rows.filter({ hasText: id2 })).toContainText(missing);
    await expect(rows.filter({ hasText: id1 })).toContainText("UNCERTAIN");
  }
});
