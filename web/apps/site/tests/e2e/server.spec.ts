import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { gzipSync } from "node:zlib";
import { writeFile } from "node:fs/promises";
test("production initial JavaScript budget and server credential boundary", async ({
  page,
}) => {
  const bodies: Promise<Buffer>[] = [];
  const external: string[] = [];
  page.on("request", (req) => {
    if (!req.url().startsWith("http://127.0.0.1:3100"))
      external.push(req.url());
  });
  page.on("response", (res) => {
    if (new URL(res.url()).pathname.endsWith(".js")) bodies.push(res.body());
  });
  await page.goto("/en");
  await page.waitForLoadState("networkidle");
  const chunks = await Promise.all(bodies);
  const bytes = chunks.reduce((sum, buf) => sum + gzipSync(buf).length, 0);
  expect(bytes).toBeLessThanOrEqual(200000);
  expect(external).toEqual([]);
  for (const chunk of chunks) {
    expect(chunk.toString()).not.toMatch(
      /CUBEJS_API_SECRET|sponsoratlas-site|CUBE_REST_URL|createHmac/,
    );
  }
  await writeFile(
    `evidence/${process.env.SITE_TEST_MODE}-browser-budget.json`,
    JSON.stringify(
      {
        context:
          "local production first navigation, gzip each loaded JS response; not CWV/RUM",
        bytes,
        limit: 200000,
        chunks: chunks.length,
        browserExternalRequests: external,
      },
      null,
      2,
    ),
  );
});
if (process.env.SITE_TEST_MODE === "fixture")
  test("fixture only: authenticated POST is cached then revalidated; backend errors remain errors", async ({
    request,
    page,
  }) => {
    const state = async () =>
      await (await request.get("http://127.0.0.1:4100/test-state")).json();
    const unauthorized = await request.post(
      "http://127.0.0.1:4100/cubejs-api/v1/load",
      { data: {} },
    );
    expect(unauthorized.status()).toBe(401);
    const start = await state();
    await request.get("/en?q=11111111");
    const first = await state();
    expect(first.qCounts["11111111"]).toBe(
      (start.qCounts["11111111"] || 0) + 1,
    );
    expect(first.validAuth).toBeGreaterThan(start.validAuth);
    await request.get("/en?q=11111111");
    expect((await state()).qCounts["11111111"]).toBe(first.qCounts["11111111"]);
    await page.waitForTimeout(2200);
    await request.get("/en?q=11111111");
    await expect
      .poll(async () => (await state()).qCounts["11111111"])
      .toBeGreaterThan(first.qCounts["11111111"]);
    const refreshed = await state();
    await request.get("http://127.0.0.1:4100/test-fail");
    try {
      await page.goto("/zh?q=22222222");
      await expect(page.locator("p[role=alert]")).toContainText(
        "数据暂时不可用",
      );
      expect(await page.locator("[data-employer-link]").count()).toBe(0);
      const axe = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
        .analyze();
      expect(axe.violations).toEqual([]);
    } finally {
      await request.get("http://127.0.0.1:4100/test-ok");
    }
    await writeFile(
      "evidence/fixture-cache-auth.json",
      JSON.stringify(
        {
          production: true,
          finiteRevalidateSeconds: 2,
          unauthenticatedStatus: 401,
          firstDelta:
            first.qCounts["11111111"] - (start.qCounts["11111111"] || 0),
          immediateSecondDelta: 0,
          afterExpiryDelta:
            refreshed.qCounts["11111111"] - first.qCounts["11111111"],
          errorHasNoSubstitute: true,
        },
        null,
        2,
      ),
    );
  });
