import "server-only";
import { createHmac } from "node:crypto";
import { dataIdentity } from "./cube-settings";
import {
  searchQuery,
  detailQuery,
  parseRows,
  unresolvedQuery,
  parseUnresolved,
} from "./data";
// All credentials and signing stay in the server module graph. A stable ten-minute
// token bucket avoids per-request cache fragmentation; tokens expire after 30min.
function settings() {
  const endpoint = process.env.CUBE_REST_URL;
  const secret = process.env.CUBEJS_API_SECRET;
  const identity = dataIdentity(process.env);
  if (!endpoint || !secret || secret.length < 32)
    throw Error("Cube server configuration unavailable");
  const url = new URL(endpoint);
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/cubejs-api/v1/load" ||
    (url.protocol !== "https:" &&
      !(url.protocol === "http:" && url.hostname === "127.0.0.1"))
  )
    throw Error("Invalid Cube endpoint");
  const seconds = Number(process.env.CUBE_REVALIDATE_SECONDS || 60);
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 3600)
    throw Error("Invalid revalidation interval");
  return { endpoint, secret, seconds, identity };
}
function token(secret: string) {
  const now = Math.floor(Date.now() / 600000) * 600;
  const encode = (obj: object) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  const value = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: "sponsoratlas-site", iat: now, exp: now + 1800 })}`;
  return `${value}.${createHmac("sha256", secret).update(value).digest("base64url")}`;
}
async function load(
  query:
    | ReturnType<typeof searchQuery>
    | ReturnType<typeof detailQuery>
    | ReturnType<typeof unresolvedQuery>,
) {
  const { endpoint, secret, seconds, identity } = settings();
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token(secret),
      // Next includes request headers in the cache key. Provenance changes
      // therefore cannot reuse an entry from another source or snapshot.
      "X-SponsorAtlas-Data-Source": identity.source,
      "X-SponsorAtlas-Data-Version": identity.version,
    },
    body: JSON.stringify({ query }),
    cache: "force-cache",
    next: { revalidate: seconds },
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw Error("Cube unavailable");
  const body = await response.text();
  if (body.length > 200000) throw Error("Cube response too large");
  return JSON.parse(body) as unknown;
}
export const searchEmployers = async (query: string) =>
  parseRows(await load(searchQuery(query)), false);
export const employerDetails = async (id: string) => {
  const rows = parseRows(await load(detailQuery(id)), true);
  if (rows.some((row) => row.id !== id))
    throw Error("Unexpected Cube identity");
  return rows;
};
export const unresolvedCount = async () =>
  parseUnresolved(await load(unresolvedQuery()));
