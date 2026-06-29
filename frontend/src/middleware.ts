import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// /jobs is intentionally public — job listings are shared catalog (CLAUDE.md rule #10).
// Unfurl bots (Twitter/LinkedIn/Discord) must reach /jobs/[id] to read OG tags + JSON-LD.
// Per-user fields (action, liked_at) are gated at the API layer via optional_user.
const PROTECTED_PATHS = [
  "/dashboard",
  "/profile",
  "/pipeline",
  "/channels",
  "/settings",
  "/notifications",
  "/admin",
];

// Backend origin used to VERIFY the session (same value the /api proxy forwards to).
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isProtected = PROTECTED_PATHS.some((p) => pathname.startsWith(p));

  if (!isProtected) return NextResponse.next();

  // Send to /login, and DELETE the (stale) cookie so we don't loop back here.
  const bounceToLogin = () => {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    const res = NextResponse.redirect(loginUrl, { status: 307 });
    res.cookies.delete("job360_session");
    return res;
  };

  const session = request.cookies.get("job360_session");
  if (!session?.value) return bounceToLogin();

  // VERIFY the cookie is real, not just present: ask the backend who it belongs to.
  // A stale/expired cookie -> 401 -> bounce to login (and clear it). This fixes the
  // bug where a leftover cookie slipped past the gate, then the API 401'd on the page.
  try {
    const verify = await fetch(`${BACKEND_ORIGIN}/api/auth/me`, {
      headers: { cookie: `job360_session=${session.value}` },
      // never cache an auth check
      cache: "no-store",
    });
    if (!verify.ok) return bounceToLogin();
  } catch {
    // Backend unreachable: fail OPEN (let the page load; its API calls surface the
    // error) rather than locking everyone out if the backend is briefly down.
    return NextResponse.next();
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/|login|register|$).*)",
  ],
};
