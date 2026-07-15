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

  // E2E test bypass — gated SOLELY on E2E_TEST_MODE, which is set ONLY by
  // playwright.config.ts's webServer env. It is never in .env.example, Railway,
  // or any real deploy, so it cannot weaken a deployed auth guard. We must NOT
  // also gate on NODE_ENV: the CI e2e runs a PRODUCTION build (npm run build &&
  // start) for speed, so NODE_ENV==="production" there too — the old non-prod
  // gate silently disabled this bypass in CI, and only the fail-open catch (now
  // fail-closed, M14) was masking it. With E2E_TEST_MODE=1 a PRESENT cookie is
  // trusted without the /api/auth/me round-trip, so hermetic (mocked, no live
  // backend) specs pass the guard deterministically.
  if (process.env.E2E_TEST_MODE === "1") {
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
    // M14: Backend unreachable — fail CLOSED. Serving the protected shell here
    // (the old "fail open" behaviour) let a stale/expired/invalid session
    // through unverified any time the auth check itself couldn't complete.
    // Redirect to login instead. We deliberately do NOT delete the cookie
    // here (unlike bounceToLogin): this is a transient network failure, not
    // proof the session is invalid, so a legitimate user shouldn't be forced
    // through a full re-login once the backend recovers — the same cookie
    // will be re-verified on their next request.
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
