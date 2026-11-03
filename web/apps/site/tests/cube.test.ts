import { test } from "node:test";
import assert from "node:assert/strict";
import { dataIdentity } from "../lib/cube-settings.ts";
test("Parquet provenance requires an immutable valid SHA256", () => {
  for (const digest of [undefined, "", "bad", "a".repeat(63), "g".repeat(64)])
    assert.throws(() =>
      dataIdentity({ SITE_DATA_SOURCE: "parquet", SITE_SNAPSHOT_SHA: digest }),
    );
  assert.deepEqual(
    dataIdentity({
      SITE_DATA_SOURCE: "parquet",
      SITE_SNAPSHOT_SHA: "a".repeat(64),
    }),
    { source: "parquet", version: "a".repeat(64) },
  );
});
test("non-Parquet source revisions are explicit and source identity stays distinct", () => {
  const version = "a".repeat(64);
  assert.deepEqual(
    dataIdentity({ SITE_DATA_SOURCE: "fixture", SITE_SOURCE_VERSION: version }),
    { source: "fixture", version },
  );
  assert.throws(() => dataIdentity({ SITE_DATA_SOURCE: "snowflake" }));
  assert.throws(() =>
    dataIdentity({
      SITE_DATA_SOURCE: "fixture",
      SITE_SOURCE_VERSION: "bad\nheader",
    }),
  );
  assert.throws(() =>
    dataIdentity({ SITE_DATA_SOURCE: "unknown", SITE_SOURCE_VERSION: "v1" }),
  );
});
