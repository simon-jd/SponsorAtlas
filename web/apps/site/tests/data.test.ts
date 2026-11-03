import { test } from "node:test";
import assert from "node:assert/strict";
import { searchQuery, detailQuery, parseRows, parseIds } from "../lib/data.ts";
test("bounded ID search excludes unresolved aggregate and owns all members", () => {
  const q = searchQuery("ER-123");
  assert.equal(q.limit, 13);
  assert.deepEqual(q.dimensions, ["lca_metrics.employer"]);
  assert.deepEqual(q.filters, [
    {
      member: "lca_metrics.employer",
      operator: "notEquals",
      values: ["UNKNOWN"],
    },
    {
      member: "lca_metrics.employer",
      operator: "contains",
      values: ["ER-123"],
    },
  ]);
  assert.throws(() => searchQuery("a".repeat(81)));
  assert.throws(() => searchQuery("' OR 1=1"));
  assert.throws(() => detailQuery("UNKNOWN"));
  assert.deepEqual(parseIds("ER-1,ER-2,ER-1"), ["ER-1", "ER-2"]);
  assert.throws(() => parseIds("ER-1,ER-2,ER-3,ER-4"));
});
test("Cube rows preserve null, fiscal period and uncertain identity history", () => {
  const row = {
    "lca_metrics.employer": "ER-1",
    "lca_metrics.case_count": "2",
    "lca_metrics.wage_p50": null,
    "lca_metrics.fiscal_year": 2025,
    "lca_metrics.employer_history_status": "UNCERTAIN",
  };
  assert.deepEqual(parseRows({ data: [row] }, true), [
    { id: "ER-1", count: 2, wage: null, year: 2025, status: "UNCERTAIN" },
  ]);
  assert.throws(() =>
    parseRows({ data: [{ ...row, "lca_metrics.case_count": "oops" }] }, true),
  );
  assert.throws(() =>
    parseRows({ data: [{ ...row, "lca_metrics.employer": "UNKNOWN" }] }, true),
  );
  assert.throws(() =>
    parseRows({ data: [{ ...row, "lca_metrics.wage_p50": -1 }] }, true),
  );
  assert.throws(() => parseRows({ error: "Continue wait" }, true));
});
test("unresolved identity stays aggregate evidence, never a search result", async () => {
  const { unresolvedQuery, parseUnresolved } = await import("../lib/data.ts");
  assert.deepEqual(unresolvedQuery().filters, [
    { member: "lca_metrics.employer", operator: "equals", values: ["UNKNOWN"] },
  ]);
  assert.equal(
    parseUnresolved({ data: [{ "lca_metrics.case_count": "37" }] }),
    37,
  );
  assert.throws(() =>
    parseUnresolved({ data: [{ "lca_metrics.case_count": null }] }),
  );
});
test("NULL history stays unknown instead of becoming a fabricated status", () => {
  const rows = parseRows(
    {
      data: [
        {
          "lca_metrics.employer": "ER-1",
          "lca_metrics.case_count": 1,
          "lca_metrics.wage_p50": null,
          "lca_metrics.fiscal_year": 2025,
          "lca_metrics.employer_history_status": null,
        },
      ],
    },
    true,
  );
  assert.equal(rows[0].status, null);
});
