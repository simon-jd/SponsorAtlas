// Shared validation for displayed provenance and the server fetch cache identity.
// Non-Parquet versions identify the configured dataset/revision, not live row age.
export function dataIdentity(env: Record<string, string | undefined>) {
  const source = env.SITE_DATA_SOURCE;
  if (source !== "parquet" && source !== "snowflake" && source !== "fixture")
    throw Error("Explicit data source required");
  const version =
    source === "parquet" ? env.SITE_SNAPSHOT_SHA : env.SITE_SOURCE_VERSION;
  if (source === "parquet") {
    if (!version || !/^[a-f0-9]{64}$/.test(version))
      throw Error("Parquet requires its verified projection SHA256");
  } else if (!version || !/^[A-Za-z0-9._:-]{1,100}$/.test(version)) {
    throw Error("Explicit dataset revision required");
  }
  return { source, version };
}
