// Single supervised lifecycle: optional true Cube process, production Next, tests.
import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { readFileSync, statSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fixtureServer } from "../apps/site/tests/fixture-server.mjs";
const mode = process.argv[2];
if (!["fixture", "actual", "red"].includes(mode))
  throw Error("Explicit fixture/actual/red required");
const root = resolve("../../..");
const secret = randomBytes(40).toString("hex");
const children = [];
let fixture;
const protectedPath = resolve(root, ".tools/facts/fy2025-full.duckdb");
const before = statSync(protectedPath); // Metadata only. Never opened, copied, or hashed.
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
async function free(port) {
  const server = createServer();
  await new Promise((ok, no) => {
    server.once("error", no);
    server.listen(port, "127.0.0.1", ok);
  });
  await new Promise((ok) => server.close(ok));
}
function launch(command, args, env = {}) {
  const child = spawn(command, args, {
    stdio: "inherit",
    env: { ...process.env, ...env },
  });
  children.push(child);
  return child;
}
async function ready(url) {
  for (let i = 0; i < 150; i++) {
    try {
      await fetch(url);
      return;
    } catch {}
    if (children.some((c) => c.exitCode !== null))
      throw Error("Service exited");
    await pause(100);
  }
  throw Error("Service readiness timeout");
}
try {
  await free(3100);
  await free(4100);
  if (mode === "actual") {
    const oracle = spawnSync(
      resolve(root, ".venv/bin/python"),
      [resolve(root, "web/scripts/site-oracle.py")],
      { stdio: "inherit" },
    );
    if (oracle.status !== 0) throw Error("Public Parquet oracle failed");
    launch(
      "bash",
      [resolve(root, "semantic/run.sh"), "serve", "parquet", "actual"],
      { CUBEJS_API_SECRET: secret, PORT: "4100" },
    );
    await ready("http://127.0.0.1:4100/cubejs-api/v1/meta");
    const status = (await fetch("http://127.0.0.1:4100/cubejs-api/v1/meta"))
      .status;
    if (status !== 403)
      throw Error("Actual Cube authentication boundary failed");
    writeFileSync(
      "evidence/actual-auth.json",
      JSON.stringify({
        endpoint: "true Cube REST meta",
        withoutJwtStatus: status,
      }) + "\n",
    );
  } else {
    fixture = fixtureServer(secret);
    await new Promise((ok) => fixture.listen(4100, "127.0.0.1", ok));
  }
  const manifest = JSON.parse(
    readFileSync(
      resolve(root, ".tools/semantic/actual/export-evidence.json"),
      "utf8",
    ),
  );
  const digest = manifest.exports.find((x) =>
    x.path.endsWith("/lca.parquet"),
  ).sha256;
  launch("pnpm", ["start"], {
    CUBEJS_API_SECRET: secret,
    CUBE_REST_URL: "http://127.0.0.1:4100/cubejs-api/v1/load",
    SITE_DATA_SOURCE: mode === "actual" ? "parquet" : "fixture",
    SITE_SOURCE_VERSION: "synthetic-contract-v1",
    SITE_SNAPSHOT_SHA: mode === "actual" ? digest : "SYNTHETIC_TEST_ONLY",
    CUBE_REVALIDATE_SECONDS: mode === "actual" ? "60" : "2",
  });
  await ready("http://127.0.0.1:3100/en");
  const test = launch(
    "pnpm",
    [
      "test:e2e",
      ...(process.env.SITE_TEST_GREP
        ? ["--grep", process.env.SITE_TEST_GREP]
        : []),
      ...(mode === "red"
        ? ["--grep", "en keyboard", "--max-failures", "1"]
        : []),
    ],
    { SITE_TEST_MODE: mode },
  );
  process.exitCode = await new Promise((ok) =>
    test.once("exit", (code) => ok(code ?? 1)),
  );
} finally {
  for (const child of children.reverse())
    if (child.exitCode === null) child.kill("SIGTERM");
  if (fixture) await new Promise((ok) => fixture.close(ok));
  await Promise.all(
    children.map(async (child) => {
      for (let i = 0; i < 40 && child.exitCode === null; i++) await pause(50);
      if (child.exitCode === null) child.kill("SIGKILL");
    }),
  );
  const after = statSync(protectedPath);
  writeFileSync(
    `evidence/${mode}-source-protection.json`,
    JSON.stringify(
      {
        operation: "stat only, no source file open/hash/copy",
        before: { size: before.size, mtimeMs: before.mtimeMs },
        after: { size: after.size, mtimeMs: after.mtimeMs },
        unchanged:
          before.size === after.size && before.mtimeMs === after.mtimeMs,
      },
      null,
      2,
    ) + "\n",
  );
  if (before.size !== after.size || before.mtimeMs !== after.mtimeMs)
    throw Error("Protected source metadata changed");
}
