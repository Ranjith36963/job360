import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// /jobs is intentionally public — job listings are shared catalog (CLAUDE.md rule #10).
// Unfurl bots (Twitter/LinkedIn/Discord) must reach /jobs/[id] to read OG tags + JSON-LD.
// Per-user fields (action, liked_at) are gated at the API layer via optional_user.
const PROTECTED_PATHS = [
  "/dashboard",
  "/profile",
  "/pipeline",
  "/settings",
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isProtected = PROTECTED_PATHS.some((p) => pathname.startsWith(p));

  if (!isProtected) return NextResponse.next();

  const session = request.cookies.get("job360_session");
  if (!session?.value) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl, { status: 307 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/|login|register|$).*)",
  ],
};
