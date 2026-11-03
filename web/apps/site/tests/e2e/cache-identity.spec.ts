import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
const a = "a".repeat(64),
  b = "b".repeat(64),
  control = "http://127.0.0.1:4100/test/configure";
if (process.env.SITE_TEST_MODE === "cache-transition") {
  for (const scenario of [
    {
      name: "source",
      query: "111",
      phases: [
        { source: "fixture", digest: a, count: 71 },
        { source: "parquet", digest: a, count: 83 },
      ],
    },
    {
      name: "snapshot",
      query: "1111",
      phases: [
        { source: "parquet", digest: a, count: 101 },
        { source: "parquet", digest: b, count: 113 },
      ],
    },
  ])
    test(`first page after ${scenario.name} change cannot relabel cached evidence`, async ({
      page,
      request,
    }) => {
      const observations = [];
      const state = async () =>
        await (await request.get("http://127.0.0.1:4100/test/state")).json();
      for (const phase of scenario.phases) {
        expect((await request.post(control, { data: phase })).ok()).toBe(true);
        await page.goto("/en?q=" + scenario.query);
        await page.locator("main[role=status]").waitFor({ state: "hidden" });
        const provenance = page.locator(".context details");
        await expect(provenance).toContainText(
          phase.source === "fixture"
            ? "SYNTHETIC TEST DATA"
            : "Verified public Parquet",
        );
        if (phase.source === "parquet")
          await expect(provenance).toContainText(phase.digest);
        const observed = await page
          .locator("[data-employer-link] .count strong")
          .innerText();
        const first = await state();
        observations.push({ ...phase, observed, backendCalls: first.calls });
        expect(first.signingBuckets).toBe(1);
        expect(first.tokensUsed).toBe(1);
        await writeFile(
          `evidence/fix1-${process.env.SITE_FIX_PHASE}-cache-${scenario.name}.json`,
          JSON.stringify(
            {
              context:
                "Synthetic REST regression modeling source/snapshot configuration; no actual data files or sources",
              sameEndpoint: true,
              sameSecret: true,
              sameBuildAndCacheDirectory: true,
              signingBuckets: first.signingBuckets,
              tokensUsed: first.tokensUsed,
              observations,
            },
            null,
            2,
          ) + "\n",
        );
        expect(observed).toBe(String(phase.count));
        await page.reload();
        await page.locator("main[role=status]").waitFor({ state: "hidden" });
        expect((await state()).calls).toBe(first.calls);
      }
    });
  for (const digest of ["", "not-a-sha256"])
    test(`Parquet digest ${digest ? "malformed" : "missing"} refuses data and provenance`, async ({
      page,
      request,
    }) => {
      const before = await (
        await request.get("http://127.0.0.1:4100/test/state")
      ).json();
      expect(
        (
          await request.post(control, {
            data: { source: "parquet", digest, count: 127 },
          })
        ).ok(),
      ).toBe(true);
      await page.goto("/en?q=11111");
      await expect(page.locator("p[role=alert]")).toContainText(
        "Data is temporarily unavailable",
      );
      await expect(page.locator("[data-employer-link]")).toHaveCount(0);
      await expect(page.locator(".context details")).toContainText(
        "Source not configured",
      );
      const after = await (
        await request.get("http://127.0.0.1:4100/test/state")
      ).json();
      expect(after.calls).toBe(before.calls);
    });
}
