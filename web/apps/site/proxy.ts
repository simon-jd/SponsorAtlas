import createMiddleware from "next-intl/middleware";
import { NextResponse, type NextRequest } from "next/server";
import { routing } from "./i18n/routing";
const intl = createMiddleware(routing);
export default function proxy(request: NextRequest) {
  const first = request.nextUrl.pathname.split("/")[1];
  // Unsupported explicit prefixes go to the app's not-found boundary, never /en/xx.
  if (first && !["en", "zh"].includes(first)) return NextResponse.next();
  return intl(request);
}
export const config = { matcher: "/((?!api|_next|.*\\..*).*)" };
