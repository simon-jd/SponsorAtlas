// Regression harness only: synthetic responses, one endpoint/secret/cache/bucket.
import http from "node:http";
import net from "node:net";
import { spawn } from "node:child_process";
import { randomBytes, createHmac, timingSafeEqual } from "node:crypto";
import { resolve } from "node:path";
const secret = randomBytes(40).toString("hex");
let site,
  test,
  phase = { source: "fixture", digest: "a".repeat(64), count: 71 },
  calls = 0;
const tokens = new Set(),
  buckets = new Set();
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
async function free(port) {
  const server = net.createServer();
  await new Promise((ok, no) => {
    server.once("error", no);
    server.listen(port, "127.0.0.1", ok);
  });
  await new Promise((ok) => server.close(ok));
}
async function stop(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  for (let i = 0; i < 50 && child.exitCode === null; i++) await pause(50);
  if (child.exitCode === null) {
    child.kill("SIGKILL");
    await new Promise((ok) => child.once("exit", ok));
  }
}
async function startSite() {
  await stop(site);
  await free(3100);
  site = spawn(
    process.execPath,
    [
      resolve("node_modules/next/dist/bin/next"),
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      "3100",
    ],
    {
      stdio: "inherit",
      env: {
        ...process.env,
        CUBEJS_API_SECRET: secret,
        CUBE_REST_URL: "http://127.0.0.1:4100/cubejs-api/v1/load",
        SITE_DATA_SOURCE: phase.source,
        SITE_SNAPSHOT_SHA: phase.digest,
        SITE_SOURCE_VERSION: phase.digest,
        CUBE_REVALIDATE_SECONDS: "3600",
      },
    },
  );
  for (let i = 0; i < 100; i++) {
    const connected = await new Promise((ok) => {
      const socket = net.connect(3100, "127.0.0.1");
      socket.once("connect", () => {
        socket.end();
        ok(true);
      });
      socket.once("error", () => ok(false));
    });
    if (connected) return;
    if (site.exitCode !== null) throw Error("Next exited");
    await pause(100);
  }
  throw Error("Next readiness timeout");
}
const backend = http.createServer(async (req, res) => {
  try {
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/test/state") {
      res.end(
        JSON.stringify({
          calls,
          tokensUsed: tokens.size,
          signingBuckets: buckets.size,
        }),
      );
      return;
    }
    let body = "";
    for await (const chunk of req) body += chunk;
    if (req.url === "/test/configure" && req.method === "POST") {
      phase = JSON.parse(body);
      await startSite();
      res.end("{}");
      return;
    }
    if (req.url !== "/cubejs-api/v1/load" || req.method !== "POST") {
      res.writeHead(404).end("{}");
      return;
    }
    const token = req.headers.authorization || "";
    const [head, payload, sig] = token.split(".");
    const expected = createHmac("sha256", secret)
      .update(head + "." + payload)
      .digest("base64url");
    if (
      !sig ||
      sig.length !== expected.length ||
      !timingSafeEqual(Buffer.from(sig), Buffer.from(expected))
    ) {
      res.writeHead(401).end("{}");
      return;
    }
    tokens.add(token);
    buckets.add(JSON.parse(Buffer.from(payload, "base64url")).iat);
    calls++;
    const { query } = JSON.parse(body);
    res.end(
      JSON.stringify({
        data: [
          query.dimensions
            ? {
                "lca_metrics.employer": "emp_" + "1".repeat(64),
                "lca_metrics.case_count": String(phase.count),
              }
            : { "lca_metrics.case_count": "0" },
        ],
      }),
    );
  } catch {
    res.writeHead(500).end('{"error":"Transition harness failure"}');
  }
});
try {
  if (600000 - (Date.now() % 600000) < 90000)
    throw Error(
      "Start this regression with at least 90 seconds left in the signing bucket; no clock mocking",
    );
  await free(3100);
  await free(4100);
  await new Promise((ok) => backend.listen(4100, "127.0.0.1", ok));
  test = spawn("pnpm", ["test:e2e", "cache-identity.spec.ts"], {
    stdio: "inherit",
    env: { ...process.env, SITE_TEST_MODE: "cache-transition" },
  });
  process.exitCode = await new Promise((ok) =>
    test.once("exit", (code) => ok(code ?? 1)),
  );
} finally {
  await stop(test);
  await stop(site);
  await new Promise((ok) => backend.close(ok));
}
