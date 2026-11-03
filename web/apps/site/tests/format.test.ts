import { test } from "node:test";
import assert from "node:assert/strict";
import { formatCurrency, formatNumber, formatDate } from "../lib/format.ts";
for (const locale of ["en", "zh"] as const)
  test(`${locale} Intl snapshot, USD and UTC date-only`, () => {
    assert.deepEqual(
      [
        formatCurrency(123456.75, locale),
        formatNumber(12345, locale),
        formatDate("2025-09-30", locale),
        formatCurrency(null, locale),
      ],
      locale === "en"
        ? ["$123,456.75", "12,345", "Sep 30, 2025", "Not available"]
        : ["US$123,456.75", "12,345", "2025年9月30日", "暂无数据"],
    );
  });
test("missing and invalid values never become zero or invalid dates", () => {
  assert.equal(formatNumber(null, "en"), "Not available");
  assert.throws(() => formatCurrency(NaN, "en"));
  assert.throws(() => formatDate("2025-02-30", "en"));
});
