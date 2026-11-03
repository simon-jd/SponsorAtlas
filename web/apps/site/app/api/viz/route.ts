import { parseScope } from '../../../lib/viz-data';
import { nativeVisualization } from '../../../lib/viz-native';
import { visualization } from '../../../lib/viz-source';
export const runtime = 'nodejs';
export async function GET(request: Request) {
  let scope;
  try {
    scope = parseScope(new URL(request.url).searchParams);
  } catch {
    return Response.json({
      error: 'Invalid scope'
    }, {
      status: 400
    });
  }
  try {
    return Response.json(process.env.SITE_DATA_SOURCE === 'snowflake' ? await nativeVisualization(scope) : visualization(scope), {
      headers: {
        'Cache-Control': 'private, no-store'
      }
    });
  } catch {
    return Response.json({
      error: 'Visualization unavailable'
    }, {
      status: 503
    });
  }
}
