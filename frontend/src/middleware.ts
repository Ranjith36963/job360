import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { safeNext } from "@/lib/safe-next";

// Job360 never sources or ranks jobs (VISION rule 4) — there is no shared
// catalog left to expose, so every one of these paths is one user's data. The old
// /settings/* and /people/* URLs are only redirects to /account, /connect and
// /outreach/people (which are protected), so they are left open to reach them.
const PROTECTED_PATHS = [
  "/profile",
  "/bring",
  "/receipts",
  "/applications",
  "/outreach",
  "/stats",
  "/needs-you",
  "/account",
  "/connect",
  "/oauth",
];

// Sign-in pages a signed-in user has no reason to see: a verified session is
// sent on to `safeNext(next)` (C4). Token pages (/reset-password, /verify-email,
// /undo-email-change, /auth/magic) stay reachable on purpose.
const AUTH_PAGES = ["/login", "/register", "/forgot-password"];

// The home page, as a list like the two above: the gate routes by membership
// of a fixed list, never by comparing the request path to one literal (CodeQL
// reads that shape as a user-controlled bypass of the session check).
const HOME_PAGES = ["/"];

const SESSION_COOKIE = "job360_session";

// Backend origin used to VERIFY the session (same value the /api proxy forwards to).
const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

// A dead backend must not hang page loads forever (S17): past this, treat it as
// an outage. 20 s, not 5: the backend is ONE worker, and a slow data call ahead
// of this check can hold it for 6-16 s on a large account (measured on prod,
// 2026-11-12). At 5 s that threw signed-in users to /login with "couldn't reach
// the server"; waiting for the page is the lesser harm.
const VERIFY_TIMEOUT_MS = 20000;

type Verdict = "valid" | "rejected" | "unavailable";

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
function e2eBypass(): boolean {
  const isProduction =
    (process.env.APP_ENV ?? "").toLowerCase() === "production" ||
    !!process.env.RAILWAY_ENVIRONMENT;
  return process.env.E2E_TEST_MODE === "1" && !isProduction;
}

// VERIFY the cookie is real, not just present: ask the backend who it belongs to.
// ONLY a definite 401 means "this session is dead". A 429 / 5xx / network error /
// timeout is the backend struggling — an outage, not a logout.
async function verifySession(value: string): Promise<Verdict> {
  try {
    const verify = await fetch(`${BACKEND_ORIGIN}/api/auth/me`, {
      headers: { cookie: `${SESSION_COOKIE}=${value}` },
      // never cache an auth check
      cache: "no-store",
      signal: AbortSignal.timeout(VERIFY_TIMEOUT_MS),
    });
    if (verify.status === 401) return "rejected";
    return verify.ok ? "valid" : "unavailable";
  } catch {
    return "unavailable";
  }
}

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const session = request.cookies.get(SESSION_COOKIE)?.value;

  // "/" is public (landing for visitors, dashboard for a session). Only a cookie
  // the backend REJECTS changes anything: drop it, so app/page.tsx renders the
  // landing instead of a dashboard full of 401s (C5). No cookie = no backend call.
  if (HOME_PAGES.includes(pathname)) {
    if (!session || e2eBypass()) return NextResponse.next();
    if ((await verifySession(session)) !== "rejected") return NextResponse.next();
    // Strip it from the forwarded request too: the page's cookies() reads that.
    request.cookies.delete(SESSION_COOKIE);
    const res = NextResponse.next({ request: { headers: request.headers } });
    res.cookies.delete(SESSION_COOKIE);
    return res;
  }

  // Sign-in pages: a signed-in user goes where they were heading (C4).
  if (AUTH_PAGES.includes(pathname)) {
    if (!session) return NextResponse.next();
    const verdict = e2eBypass() ? "valid" : await verifySession(session);
    if (verdict === "valid") {
      const url = new URL(safeNext(request.nextUrl.searchParams.get("next")), request.url);
      // next=/login would send a signed-in user straight back here: a loop.
      if (AUTH_PAGES.includes(url.pathname)) url.href = new URL(safeNext(null), request.url).href;
      return NextResponse.redirect(url, { status: 307 });
    }
    const res = NextResponse.next();
    if (verdict === "rejected") res.cookies.delete(SESSION_COOKIE);
    return res;
  }

  const isProtected = PROTECTED_PATHS.some((p) => pathname.startsWith(p));
  if (!isProtected) return NextResponse.next();

  // The full return path, query string included (S12), in the contract's shape:
  // /login?reason=expired&next=<encodeURIComponent(pathname + search)>.
  const next = encodeURIComponent(pathname + search);

  // Send to /login, and DELETE the (stale) cookie so we don't loop back here.
  // `reason=expired` only when a cookie was present and the backend rejected it (C7).
  const bounceToLogin = (expired: boolean) => {
    const loginUrl = new URL("/login", request.url);
    loginUrl.search = `${expired ? "reason=expired&" : ""}next=${next}`;
    const res = NextResponse.redirect(loginUrl, { status: 307 });
    res.cookies.delete(SESSION_COOKIE);
    return res;
  };

  if (!session) return bounceToLogin(false);
  if (e2eBypass()) return NextResponse.next();

  const verdict = await verifySession(session);
  // A stale/expired cookie -> 401 -> bounce to login (and clear it). This fixes the
  // bug where a leftover cookie slipped past the gate, then the API 401'd on the page.
  if (verdict === "rejected") return bounceToLogin(true);
  if (verdict === "unavailable") {
    // Backend unreachable: fail CLOSED (docs/fable/03 F4) — an UNVERIFIED
    // session must not grant access to a protected page. Deliberately DIFFERENT
    // from bounceToLogin(): the cookie is NOT deleted (the outage is ours; the
    // session may be perfectly valid once the backend recovers), and an error
    // marker tells the login page to explain rather than look like a logout.
    const loginUrl = new URL("/login", request.url);
    loginUrl.search = `next=${next}&error=service_unavailable`;
    return NextResponse.redirect(loginUrl, { status: 307 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/).*)"],
};
