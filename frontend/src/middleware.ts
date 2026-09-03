import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// /jobs is intentionally public — job listings are shared catalog (CLAUDE.md rule #10).
// Unfurl bots (Twitter/LinkedIn/Discord) must reach /jobs/[id] to read OG tags + JSON-LD.
// Per-user fields (action, liked_at) are gated at the API layer via optional_user.
const PROTECTED_PATHS = [
  "/dashboard",
  "/profile",
  "/pipeline",
  "/bring",
  "/receipts",
  "/channels",
  "/settings",
  "/notifications",
  "/admin",
  "/oauth",
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

  // E2E test bypass — with E2E_TEST_MODE=1 a PRESENT cookie is trusted without
  // the /api/auth/me round-trip, so hermetic (mocked, no live backend) specs pass
  // deterministically. Set ONLY by playwright.config.ts's webServer env.
  //
  // F2 hardening — a single `E2E_TEST_MODE==="1"` gate meant one stray env var in
  // a real deploy would silently disable auth for everyone. NODE_ENV can't be the
  // guard (CI e2e runs a PROD build, so NODE_ENV==="production" there too — it
  // would kill the bypass in CI). So we require the ABSENCE of a production signal,
  // matching the backend's OWN prod-detection exactly (api/middleware.py
  // `_is_production`): APP_ENV==="production" OR RAILWAY_ENVIRONMENT set. Checking
  // both — not just RAILWAY_ENVIRONMENT — means a non-Railway prod deploy (Vercel /
  // Docker / bare VM, where APP_ENV=production is the general signal) with a stray
  // E2E_TEST_MODE=1 also fails closed. CI/local set neither, so the bypass still
  // works there.
  // Case-insensitive like the backend's _is_production() (.lower() there) —
  // APP_ENV=Production must count as prod on BOTH sides, or the bypass gate
  // and the cookie-Secure/HSTS gate disagree about what "production" means.
  const isProduction =
    (process.env.APP_ENV ?? "").toLowerCase() === "production" ||
    !!process.env.RAILWAY_ENVIRONMENT;
  if (process.env.E2E_TEST_MODE === "1" && !isProduction) {
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
