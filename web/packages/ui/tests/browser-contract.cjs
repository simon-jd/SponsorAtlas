const { injectAxe, checkA11y } = require("axe-playwright");
const { strict: assert } = require("node:assert");

const path = require("node:path");
const fs = require("node:fs/promises");
const config = {
  async preVisit(page, context) {
    if (context.name === "Narrow")
      await page.setViewportSize({ width: 390, height: 844 });
    page.setDefaultTimeout(2000);
    await page.emulateMedia({ reducedMotion: "reduce" });
  },
  async postVisit(page, context) {
    const root = page.locator("#storybook-root");
    await injectAxe(page);
    const screenshot = async (name) => {
      const directory = path.resolve(
        __dirname,
        "../../../evidence/screenshots",
      );
      await fs.mkdir(directory, { recursive: true });
      await page.screenshot({
        path: path.join(directory, name + ".png"),
        fullPage: true,
      });
    };
    const audit = () =>
      checkA11y(page, "#storybook-root", {
        detailedReport: true,
        detailedReportOptions: { html: true },
        axeOptions: {
          runOnly: {
            type: "tag",
            values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"],
          },
        },
      });
    const focused = async (locator) =>
      assert.equal(
        await locator.evaluate((el) => el === document.activeElement),
        true,
      );
    await audit();
    if (context.title === "Components/Button") {
      const button = root.getByRole("button", { name: "Compare employers" });
      await button.focus();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Space");
      assert.equal(await root.getByLabel("Selected count").textContent(), "2");
      await audit();
      await page.keyboard.press("Tab");
      assert.equal(
        await root.getByRole("button", { name: "Unavailable" }).isDisabled(),
        true,
      );
    }
    if (context.title === "Components/Combobox") {
      const input = root.getByRole("combobox");
      if (context.name === "Disabled") {
        assert.equal(await input.isDisabled(), true);
        await root.getByRole("button", { name: "Next control" }).focus();
        await focused(root.getByRole("button", { name: "Next control" }));
      } else {
        await input.focus();
        await page.keyboard.press("ArrowDown");
        assert.equal(await input.getAttribute("aria-expanded"), "true");
        await audit();
        if (["Keyboard", "Dark"].includes(context.name))
          await screenshot("combobox-open-" + context.name.toLowerCase());
        if (context.name === "Empty")
          assert.equal(
            await root
              .getByRole("status")
              .filter({ hasText: "No employers found" })
              .textContent(),
            "No employers found",
          );
        else {
          await page.keyboard.press("ArrowDown");
          await page.keyboard.press("Enter");
          assert.equal(
            await root.getByLabel("Chosen employer").textContent(),
            "cedar",
          );
          await page.keyboard.press("ArrowDown");
          await page.keyboard.press("End");
          const id = await input.getAttribute("aria-activedescendant");
          assert.equal(
            await page.locator(`[id="${id}"]`).textContent(),
            "Cedar Labs",
          );
        }
        await page.keyboard.press("Escape");
        assert.equal(await input.getAttribute("aria-expanded"), "false");
        await focused(input);
        if (context.name !== "Empty") {
          await input.fill("");
          await page.keyboard.press("ArrowUp");
          const lastId = await input.getAttribute("aria-activedescendant");
          assert.equal(
            await page.locator(`[id="${lastId}"]`).textContent(),
            "Cedar Labs",
          );
        }
        await input.fill("zzzz");
        assert.equal(
          await root
            .getByRole("status")
            .filter({ hasText: "No employers found" })
            .textContent(),
          "No employers found",
        );
        await audit();
        await page.keyboard.press("Tab");
        await focused(root.getByRole("button", { name: "Next control" }));
        assert.equal(await input.getAttribute("aria-expanded"), "false");
        if (context.name === "Error")
          assert.equal(await input.getAttribute("aria-invalid"), "true");
      }
    }
    if (context.title === "Components/DataTable") {
      const table = root.getByRole("table");
      if (context.name === "Narrow") {
        const scroll = root.getByRole("region");
        await scroll.focus();
        await focused(scroll);
        assert.ok(
          await scroll.evaluate((el) => el.scrollWidth > el.clientWidth),
        );
        await page.keyboard.press("ArrowRight");
        await page.waitForFunction(
          () => document.querySelector(".sa-table-scroll").scrollLeft > 0,
        );
        await page.keyboard.press("Tab");
        await focused(root.getByRole("button", { name: "Employer" }));
      }
      assert.ok(
        (await table.getAttribute("aria-label")) ||
          (await table.locator("caption").textContent()),
      );
      if (context.name === "Empty")
        assert.ok(
          await root.getByText("No filings for these filters").isVisible(),
        );
      else {
        const sort = root.getByRole("button", { name: "Employer" });
        await sort.focus();
        await page.keyboard.press("Enter");
        assert.equal(
          await sort.locator("..").getAttribute("aria-sort"),
          "ascending",
        );
        assert.equal(
          await table
            .locator("tbody tr")
            .first()
            .locator("td")
            .first()
            .textContent(),
          "Atlas Research",
        );
        await page.keyboard.press("Enter");
        assert.equal(
          await sort.locator("..").getAttribute("aria-sort"),
          "descending",
        );
        await page.keyboard.press("Tab");
        await focused(root.getByRole("button", { name: "LCA filings" }));
      }
      await audit();
    }
    if (
      context.title === "Components/ChartFrame" ||
      context.title === "Components/MapContainer"
    ) {
      const isChart = context.title.endsWith("ChartFrame");
      const region = root.getByRole("region", {
        name: isChart ? "Annual LCA filings" : "Filings by region",
        exact: true,
      });
      assert.equal(await region.count(), 1);
      const summary = root.locator("summary");
      await summary.focus();
      await page.keyboard.press("Enter");
      assert.equal(await root.locator("details").getAttribute("open"), "");
      assert.ok(
        await root
          .getByText(
            context.name === "Unavailable"
              ? isChart
                ? "No data available"
                : "No region data available"
              : isChart
                ? "2025"
                : "California: 42 filings",
            { exact: true },
          )
          .isVisible(),
      );
      await audit();
      await page.keyboard.press("Space");
      assert.equal(await root.locator("details").getAttribute("open"), null);
      await focused(summary);
    }
    if (context.title === "Components/Tooltip") {
      const trigger = root.getByRole("button", { name: "About filings" });
      await trigger.focus();
      assert.ok(await root.getByRole("tooltip").isVisible());
      assert.ok(await trigger.getAttribute("aria-describedby"));
      await trigger.hover();
      await page.mouse.move(900, 700);
      assert.ok(await root.getByRole("tooltip").isVisible());
      await audit();
      await page.keyboard.press("Escape");
      assert.equal(await root.getByRole("tooltip").count(), 0);
      await focused(trigger);
      await page.keyboard.press("Tab");
      await focused(root.getByRole("button", { name: "Next control" }));
      await trigger.hover();
      assert.ok(await root.getByRole("tooltip").isVisible());
      await root.getByRole("tooltip").hover();
      assert.ok(await root.getByRole("tooltip").isVisible());
      await page.keyboard.press("Escape");
      assert.equal(await root.getByRole("tooltip").count(), 0);
    }
    if (context.title === "Components/Tabs") {
      const first = root.getByRole("tab", { name: "Overview" });
      await first.focus();
      await page.keyboard.press("ArrowRight");
      await focused(root.getByRole("tab", { name: "Wages" }));
      assert.equal(
        await root
          .getByRole("tab", { name: "Wages" })
          .getAttribute("aria-selected"),
        "true",
      );
      assert.ok(
        await root.getByRole("tabpanel", { name: "Wages" }).isVisible(),
      );
      await audit();
      await page.keyboard.press("End");
      await focused(root.getByRole("tab", { name: "Sources" }));
      await page.keyboard.press("ArrowRight");
      await focused(first);
      await page.keyboard.press("ArrowLeft");
      await focused(root.getByRole("tab", { name: "Sources" }));
      await page.keyboard.press("Home");
      await focused(first);
      await page.keyboard.press("Tab");
      await focused(root.getByRole("tabpanel", { name: "Overview" }));
      if (context.name === "Narrow") await screenshot("tabs-narrow-focus");
    }
    if (context.title === "Foundations/Tokens") {
      const style = await root.getByTestId("token-probe").evaluate((el) => {
        const s = getComputedStyle(el);
        return {
          background: s.backgroundColor,
          color: s.color,
          padding: s.padding,
          fontSize: s.fontSize,
          radius: s.borderRadius,
          duration: s.transitionDuration,
        };
      });
      const expected = {
        background:
          context.name === "Dark" ? "rgb(26, 43, 64)" : "rgb(255, 255, 255)",
        color:
          context.name === "Dark" ? "rgb(243, 246, 251)" : "rgb(23, 43, 69)",
        padding: "16px",
        fontSize: "20px",
        radius: "12px",
        duration: "0s",
      };
      for (const [key, value] of Object.entries(expected))
        assert.equal(style[key], value, key);
    }
    if (context.title === "Design/References") {
      assert.equal(await root.getByRole("heading", { level: 1 }).count(), 1);
      await screenshot("reference-" + context.name.toLowerCase());
    }
    if (context.name === "Narrow")
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
        "No viewport overflow at390px",
      );
    await audit();
  },
};
module.exports = config;
