import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { RETURN_COOKIE, RETURN_COOKIE_MAX_AGE, isConsentPath } from "@/lib/safe-next";

// Job360 never sources or ranks jobs (VISION rule 4) — there is no shared
// catalog left to expose, so every one of these paths is one user's data.
const PROTECTED_PATHS = [
  "/profile",
  "/bring",
  "/receipts",
  "/applications",
  "/people",
  "/stats",
  "/needs-you",
  "/settings",
  "/oauth",
];

// Backend origin used to VERIFY the session (same value the /api proxy forwards to).
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

// The ONE production signal in this file, matching the backend's
// `_is_production()` (api/middleware.py). Read per REQUEST, never at module
// load, so the value is the live env. NODE_ENV is deliberately not an arm —
// see the long comment on the E2E bypass below for why it lies in CI.
function isProductionEnv(): boolean {
  return (
    (process.env.APP_ENV ?? "").toLowerCase() === "production" ||
    !!process.env.RAILWAY_ENVIRONMENT
  );
}

// Remember an OAuth consent URL across a login that lost its ?next (see safe-next.ts).
//
// Two guards, both from review of #701:
//
//   * SET ONLY ON A REAL TOP-LEVEL NAVIGATION. `/oauth/authorize` is
//     unauthenticated and mints a pending `rid` for any registered client
//     (backend/src/api/routes/oauth.py), so an attacker can always hold a live
//     /oauth/consent/<rid> URL. This cookie used to be set by ANY unauthenticated
//     GET of such a path — including an <img>/<iframe> on a third-party page —
//     which silently planted a consent destination in a passing visitor's
//     browser and hijacked their NEXT, unrelated sign-in. `Sec-Fetch-Dest:
//     document` is sent only for a top-level document load; a subresource gets
//     image/iframe/empty and is refused here. A MISSING header still passes:
//     fetch-metadata is a progressive signal, and a client old enough to omit it
//     is not the browser cookie jar this attack needs.
//
//   * NEVER OUTLIVE THE FLOW THAT CREATED IT. Any other protected bounce expires
//     a leftover cookie, so an abandoned consent flow cannot ambush a later
//     plain login inside the 30-minute TTL.
function rememberConsentReturn(
  request: NextRequest,
  res: NextResponse,
  pathname: string
): void {
  if (!isConsentPath(pathname)) {
    // Conditional on purpose: an unconditional expiry would put a Set-Cookie on
    // every single bounce for a cookie that is almost never there.
    if (request.cookies.has(RETURN_COOKIE)) {
      res.cookies.set(RETURN_COOKIE, "", { path: "/", maxAge: 0, sameSite: "lax" });
    }
    return;
  }
  const dest = request.headers.get("sec-fetch-dest");
  if (dest && dest !== "document") return;
  res.cookies.set(RETURN_COOKIE, pathname, {
    path: "/",
    maxAge: RETURN_COOKIE_MAX_AGE,
    sameSite: "lax",
    secure: isProductionEnv(),
  });
}

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
    rememberConsentReturn(request, res, pathname);
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
  // isProductionEnv() is the single definition both gates now read; it used to
  // be spelled out twice and the two copies had already drifted.
  if (process.env.E2E_TEST_MODE === "1" && !isProductionEnv()) {
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
    // ONLY a definite 401 means "this session is dead". A 429 / 5xx is the
    // backend struggling — treat it like an outage (below): keep the cookie.
    if (verify.status === 401) return bounceToLogin();
    if (!verify.ok) throw new Error(`auth verify failed: ${verify.status}`);
  } catch {
    // Backend unreachable: fail CLOSED (docs/fable/03 F4) — an UNVERIFIED
    // session must not grant access to a protected page. Deliberately DIFFERENT
    // from bounceToLogin(): the cookie is NOT deleted (the outage is ours; the
    // session may be perfectly valid once the backend recovers), and an error
    // marker tells the login page to explain rather than look like a logout.
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    loginUrl.searchParams.set("error", "service_unavailable");
    const res = NextResponse.redirect(loginUrl, { status: 307 });
    rememberConsentReturn(request, res, pathname);
    return res;
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/|login|register|$).*)",
  ],
};
