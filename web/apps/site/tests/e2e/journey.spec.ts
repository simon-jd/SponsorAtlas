import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, readFile } from "node:fs/promises";
import { formatCurrency, formatNumber } from "../../lib/format";
// Tab to a target using keyboard events only. No focus(), mouse, or script activation.
async function tabTo(page: Page, selector: string) {
  await page.locator(selector).first().waitFor({ state: "visible" });
  for (let i = 0; i < 45; i++) {
    if (
      await page
        .locator(selector)
        .evaluateAll((nodes) =>
          nodes.some((node) => node === document.activeElement),
        )
    )
      return;
    await page.keyboard.press("Tab");
  }
  throw Error(`Keyboard could not reach ${selector}`);
}
async function accessible(page: Page) {
  // Next streams page content behind the loading boundary before revealing it.
  // Audit the completed visible page, not the transient hidden stream container.
  await page.locator("main[role=status]").waitFor({ state: "hidden" });
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(result.violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}
for (const locale of ["en", "zh"])
  test(`${locale} keyboard search, employer, compare; three pages axe, themes and narrow`, async ({
    page,
  }) => {
    await page.goto(`/${locale}`);
    await expect(page.locator("html")).toHaveAttribute("lang", locale);
    await expect(page).toHaveTitle(
      locale === "zh"
        ? "SponsorAtlas · 公开 LCA 数据"
        : "SponsorAtlas · Public LCA evidence",
    );
    await accessible(page);
    await mkdir("evidence/screenshots", { recursive: true });
    await page.screenshot({
      path: `evidence/screenshots/${process.env.SITE_EVIDENCE_PREFIX || process.env.SITE_TEST_MODE}-${locale}-search.png`,
      fullPage: true,
    });
    const id = await page
      .locator("[data-employer-link]")
      .first()
      .getAttribute("data-employer-link");
    expect(id).toBeTruthy();
    await tabTo(page, "input[name=q]");
    await page.keyboard.type(id!);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`q=${id}`));
    await expect(page.locator("[data-employer-link]")).toHaveCount(1);
    await tabTo(page, "[data-employer-link]");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/employers/${id}`));
    await accessible(page);
    const geometry = await page
      .locator(".sa-table-scroll")
      .evaluate((node) => ({
        scroll: node.scrollWidth,
        client: node.clientWidth,
        columns: Array.from(node.querySelectorAll("th")).map((th) => ({
          width: th.clientWidth,
          whiteSpace: getComputedStyle(th).whiteSpace,
        })),
      }));
    expect(geometry.scroll, JSON.stringify(geometry)).toBeLessThanOrEqual(
      geometry.client,
    );
    await page.screenshot({
      path: `evidence/screenshots/${process.env.SITE_EVIDENCE_PREFIX || process.env.SITE_TEST_MODE}-${locale}-detail.png`,
      fullPage: true,
    });
    await tabTo(page, "[data-add-compare]");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/compare\\?ids=${id}`));
    await expect(page.locator("table")).toContainText(id!);
    await accessible(page);
    await mkdir("evidence/screenshots", { recursive: true });
    await page.screenshot({
      path: `evidence/screenshots/${process.env.SITE_EVIDENCE_PREFIX || process.env.SITE_TEST_MODE}-${locale}-compare.png`,
      fullPage: true,
    });
    await tabTo(page, "[data-theme-toggle]");
    await page.keyboard.press("Enter");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.setViewportSize({ width: 390, height: 844 });
    await accessible(page);
    await tabTo(page, ".sa-table-scroll");
    await page.keyboard.press("ArrowRight");
    await expect
      .poll(() =>
        page.locator(".sa-table-scroll").evaluate((node) => node.scrollLeft),
      )
      .toBeGreaterThan(0);
    await page.screenshot({
      path: `evidence/screenshots/${process.env.SITE_EVIDENCE_PREFIX || process.env.SITE_TEST_MODE}-${locale}-dark-narrow.png`,
      fullPage: true,
    });
    await tabTo(page, "[data-language]");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(
      new RegExp(`/${locale === "en" ? "zh" : "en"}/compare\\?ids=${id}`),
    );
    await expect(page.locator("table")).toContainText(id!);
    // Also inspect detail and search in the persisted dark theme at mobile width.
    await page.goto(`/${locale}/employers/${id}`);
    await accessible(page);
    await page.goto(`/${locale}`);
    await accessible(page);
  });
test("empty, malformed input, invalid locale and missing employer are honest", async ({
  page,
}) => {
  await page.goto("/en?q=NOT-AN-EMPLOYER");
  await expect(page.locator("p[role=status]")).toContainText("No employers");
  await accessible(page);
  await page.goto("/zh?q=%27");
  await expect(page.locator("p[role=alert]")).toContainText("标识符");
  await accessible(page);
  await page.goto("/en/employers/UNKNOWN");
  await expect(page.locator("h1")).toContainText("not found");
  const invalid = await page.goto("/xx");
  expect(invalid?.status()).toBe(404);
  await page.goto("/en/compare");
  await expect(page.locator("p[role=status]")).toContainText("Choose");
});

if (process.env.SITE_TEST_MODE === "fixture")
  test("fixture periods, unresolved aggregate, uncertainty, missing wage and multiple comparison", async ({
    page,
  }) => {
    const a = "emp_" + "1".repeat(64),
      b = "emp_" + "2".repeat(64);
    await page.goto("/en");
    await expect(page.locator(".quality")).toContainText("37");
    await page.goto(`/en/compare?ids=${a},${b}`);
    await expect(page.locator("table")).toContainText("UNCERTAIN");
    await expect(page.locator("table")).toContainText("2025");
    await expect(page.locator("table")).toContainText("Not available");
    await expect(page.locator("table")).toContainText("$120,000.00");
    await page.goto("/en/compare?ids=ER-1,ER-2,ER-3,ER-4");
    await expect(page.locator("p[role=alert]")).toContainText("identifier");
  });

if (process.env.SITE_TEST_MODE === "actual")
  test("true Cube RSC output matches independent public Parquet SQL", async ({
    page,
  }) => {
    const reference = JSON.parse(
      await readFile("evidence/actual-reference.json", "utf8"),
    );
    await page.goto("/en");
    await expect(page.locator("[data-employer-link]").first()).toHaveAttribute(
      "data-employer-link",
      reference.id,
    );
    await expect(page.locator("[data-employer-link]").first()).toContainText(
      formatNumber(reference.count, "en"),
    );
    await expect(page.locator(".quality")).toContainText(
      formatNumber(reference.unresolved, "en"),
    );
    await page.goto(`/en/employers/${reference.id}`);
    const rows = page.locator("tbody tr");
    await expect(rows).toHaveCount(reference.rows.length);
    for (let i = 0; i < reference.rows.length; i++) {
      const row = reference.rows[i];
      await expect(rows.nth(i)).toContainText(String(row.year));
      await expect(rows.nth(i)).toContainText(row.status);
      await expect(rows.nth(i)).toContainText(formatNumber(row.count, "en"));
      await expect(rows.nth(i)).toContainText(formatCurrency(row.wage, "en"));
    }
  });
