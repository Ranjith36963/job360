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

  // E2E test bypass — NEVER active in production. When the Playwright webServer
  // sets E2E_TEST_MODE=1 (and NODE_ENV is not "production"), a PRESENT cookie is
  // trusted without the server-side /api/auth/me round-trip, so the hermetic
  // (mocked, no live backend) specs pass the guard deterministically instead of
  // relying on the fail-open catch below. Double-gated (env flag + non-prod) so
  // it can never weaken the real auth guard in a deployed build.
  if (process.env.NODE_ENV !== "production" && process.env.E2E_TEST_MODE === "1") {
    return NextResponse.next();
  }

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
    // Backend unreachable: fail CLOSED (docs/fable/03 F4) — an UNVERIFIED
    // session must not grant access to a protected page. Deliberately DIFFERENT
    // from bounceToLogin(): the cookie is NOT deleted (the outage is ours; the
    // session may be perfectly valid once the backend recovers), and an error
    // marker tells the login page to explain rather than look like a logout.
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    loginUrl.searchParams.set("error", "service_unavailable");
    return NextResponse.redirect(loginUrl, { status: 307 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/|login|register|$).*)",
  ],
};
