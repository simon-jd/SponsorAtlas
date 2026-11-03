import 'server-only';
import { createHmac } from 'node:crypto';
import { dataIdentity } from './cube-settings.ts';
import { parseNativeAggregate, type Scope } from './viz-data.ts';
export async function nativeVisualization(scope: Scope, transport: typeof fetch = fetch) {
  const identity = dataIdentity(process.env),
    secret = process.env.CUBEJS_API_SECRET;
  if (identity.source !== 'snowflake' || !secret || secret.length < 32) throw Error('Native configuration unavailable');
  const url = new URL(process.env.CUBE_REST_URL || '');
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/cubejs-api/v1/load' || url.protocol !== 'https:' && !(url.protocol === 'http:' && url.hostname === '127.0.0.1')) throw Error('Invalid native endpoint');
  url.pathname = '/cubejs-api/v1/h3';
  const encode = (obj: object) => Buffer.from(JSON.stringify(obj)).toString('base64url');
  const now = Math.floor(Date.now() / 1000);
  const value = `${encode({
    alg: 'HS256',
    typ: 'JWT'
  })}.${encode({
    sub: 'sponsoratlas-site',
    iat: now,
    exp: now + 60
  })}`;
  const token = `${value}.${createHmac('sha256', secret).update(value).digest('base64url')}`;
  const response = await transport(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: token,
      'X-SponsorAtlas-Data-Source': identity.source,
      'X-SponsorAtlas-Data-Version': identity.version
    },
    body: JSON.stringify(scope),
    cache: 'no-store',
    signal: AbortSignal.timeout(15000)
  });
  if (!response.ok || !response.body) throw Error('Native aggregate unavailable');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const {
        done,
        value
      } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 2000000) throw Error('Native aggregate byte bound');
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel();
    throw error;
  }
  return parseNativeAggregate(JSON.parse(Buffer.concat(chunks).toString()), scope, identity);
}
