import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
for (const theme of ["light", "dark"])
  test(`result card focus is visibly inside its clip in ${theme}`, async ({
    page,
  }) => {
    await page
      .context()
      .addCookies([
        { name: "sa-theme", value: theme, url: "http://127.0.0.1:3100" },
      ]);
    await page.goto("/en");
    await page.locator("main[role=status]").waitFor({ state: "hidden" });
    const link = page.locator("[data-employer-link]").first();
    await expect(link).toBeVisible();
    for (
      let n = 0;
      n < 30 &&
      !(await link.evaluate((node) => node === document.activeElement));
      n++
    )
      await page.keyboard.press("Tab");
    await expect(link).toBeFocused();
    const style = await link.evaluate((node) => ({
      color: getComputedStyle(node).outlineColor,
      background: getComputedStyle(node.parentElement!).backgroundColor,
    }));
    await mkdir("evidence/screenshots", { recursive: true });
    const shot = await link.screenshot({
      path: `evidence/screenshots/fix1-${process.env.SITE_FIX_PHASE || process.env.SITE_TEST_MODE}-${theme}-card-focus.png`,
    });
    // Inspect actual rendered pixels within the card's screenshot, away from text
    // and rounded corners. An outside clipped outline cannot satisfy this check.
    const pixels = await page.evaluate(
      async ({ png, color }) => {
        const img = new Image();
        img.src = "data:image/png;base64," + png;
        await img.decode();
        const canvas = document.createElement("canvas");
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext("2d")!;
        ctx.drawImage(img, 0, 0);
        const rgb = color.match(/\d+/g)!.slice(0, 3).map(Number);
        const data = ctx.getImageData(
          0,
          Math.floor(img.height / 2),
          12,
          1,
        ).data;
        let matching = 0;
        for (let x = 0; x < 12; x++)
          if (rgb.every((v, i) => Math.abs(data[x * 4 + i] - v) <= 2))
            matching++;
        return matching;
      },
      { png: shot.toString("base64"), color: style.color },
    );
    expect(
      pixels,
      "At least two pixels of focus-colored ring must be visible inside the clipped left edge",
    ).toBeGreaterThanOrEqual(2);
    const luminance = (color: string) => {
      const rgb = color
        .match(/\d+/g)!
        .slice(0, 3)
        .map((v) => {
          const n = Number(v) / 255;
          return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
        });
      return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
    };
    const a = luminance(style.color),
      b = luminance(style.background);
    expect(
      (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
    ).toBeGreaterThanOrEqual(3);
  });
