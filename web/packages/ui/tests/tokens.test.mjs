import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { compile } from "tailwindcss";
const css = await readFile(
  new URL("../src/generated/tokens.css", import.meta.url),
  "utf8",
);
const theme = await readFile(
  new URL("../src/generated/tailwind.css", import.meta.url),
  "utf8",
);
test("both color themes and all token groups are generated", () => {
  for (const value of [
    "--sa-color-bg: #f5f7fa",
    "--sa-color-bg: #101c2c",
    "--sa-space-4: 1rem",
    "--sa-font-size-lg: 1.25rem",
    "--sa-radius-md: 0.75rem",
    "--sa-motion-fast: 0.12s",
  ])
    assert.ok(css.includes(value), value);
  assert.ok(css.includes('[data-theme="dark"]'));
});
test("Tailwind consumes generated aliases as real utility CSS", async () => {
  const result = await compile(theme + "\n@tailwind utilities;");
  const output = result.build([
    "bg-surface",
    "text-ink",
    "p-4",
    "text-lg",
    "rounded-md",
    "duration-fast",
  ]);
  for (const value of [
    "background-color: var(--sa-color-surface)",
    "color: var(--sa-color-ink)",
    "padding: var(--sa-space-4)",
    "font-size: var(--sa-font-size-lg)",
    "border-radius: var(--sa-radius-md)",
    "--tw-duration: var(--sa-motion-fast)",
  ])
    assert.ok(output.includes(value), value + "\n" + output);
});
