// Explicit synthetic REST contract fixture. Never imported by product code.
import http from "node:http";
import { createHmac, timingSafeEqual } from "node:crypto";
export const ids = ["emp_" + "1".repeat(64), "emp_" + "2".repeat(64)];
export function fixtureServer(secret) {
  let calls = 0,
    validAuth = 0,
    mode = "ok";
  const qCounts = {};
  return http.createServer(async (req, res) => {
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/test-state") {
      res.end(JSON.stringify({ calls, validAuth, qCounts }));
      return;
    }
    if (req.url === "/test-ok") {
      mode = "ok";
      res.end("{}");
      return;
    }
    if (req.url === "/test-fail") {
      mode = "fail";
      res.end("{}");
      return;
    }
    if (req.url !== "/cubejs-api/v1/load" || req.method !== "POST") {
      res.writeHead(404).end("{}");
      return;
    }
    calls++;
    const token = req.headers.authorization || "";
    const [head, body, sig] = token.split(".");
    const expected = createHmac("sha256", secret)
      .update(head + "." + body)
      .digest("base64url");
    if (
      !sig ||
      sig.length !== expected.length ||
      !timingSafeEqual(Buffer.from(sig), Buffer.from(expected)) ||
      JSON.parse(Buffer.from(body, "base64url")).exp < Date.now() / 1000
    ) {
      res.writeHead(401).end("{}");
      return;
    }
    validAuth++;
    if (mode === "fail") {
      res.writeHead(503).end("{}");
      return;
    }
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const { query } = JSON.parse(raw);
    const key =
      query.filters.find(
        (f) => f.operator === "equals" || f.operator === "contains",
      )?.values[0] || "all";
    qCounts[key] = (qCounts[key] || 0) + 1;
    if (!query.dimensions) {
      res.end(JSON.stringify({ data: [{ "lca_metrics.case_count": "37" }] }));
      return;
    }
    const detail = query.dimensions.includes("lca_metrics.fiscal_year");
    const filter = query.filters.find(
      (f) => f.operator === "equals" || f.operator === "contains",
    );
    const selected = ids.filter(
      (id) =>
        !filter ||
        (filter.operator === "equals"
          ? id === filter.values[0]
          : id.includes(filter.values[0])),
    );
    const data = selected.map((id, i) => ({
      "lca_metrics.employer": id,
      "lca_metrics.case_count": String(20 - i),
      ...(detail
        ? {
            "lca_metrics.fiscal_year": 2025,
            "lca_metrics.employer_history_status": id === ids[1] ? null : "UNCERTAIN",
            "lca_metrics.wage_p50": id === ids[1] ? null : "120000",
          }
        : {}),
    }));
    res.end(JSON.stringify({ data }));
  });
}
