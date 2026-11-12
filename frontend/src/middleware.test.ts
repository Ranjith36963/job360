/**
 * Middleware auth guard — fail-closed on backend outage (docs/fable/03 F4).
 *
 * The guard used to fail OPEN when the backend was unreachable: an unverified
 * session cookie granted access to protected pages. These tests pin the new
 * behaviour: outage → redirect to /login with error=service_unavailable, and —
 * deliberately unlike a 401 bounce — WITHOUT deleting the cookie (the outage is
 * ours; the session may be perfectly valid once the backend recovers).
 */
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { config, middleware } from "./middleware";

// /profile stands in as "any protected route" here (PROTECTED_PATHS).
function protectedRequest(): NextRequest {
  return new NextRequest("http://localhost:3000/profile", {
    headers: { cookie: "job360_session=some-session-value" },
  });
}

describe("middleware — backend outage (F4)", () => {
  beforeEach(() => {
    vi.stubEnv("E2E_TEST_MODE", "");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("connect ECONNREFUSED"))
    );
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("fails CLOSED: redirects a protected route to /login", async () => {
    const res = await middleware(protectedRequest());
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/profile");
    expect(location.searchParams.get("error")).toBe("service_unavailable");
  });

  it("does NOT delete the session cookie on outage", async () => {
    const res = await middleware(protectedRequest());
    // A 401 bounce clears the cookie; the outage bounce must not — the session
    // may still be valid when the backend comes back.
    const setCookie = res.headers.get("set-cookie") ?? "";
    expect(setCookie).not.toMatch(/job360_session=;/);
  });

  it("still bounces to login (cookie cleared) on a real 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 401 }))
    );
    const res = await middleware(protectedRequest());
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("error")).toBeNull();
    expect(res.headers.get("set-cookie") ?? "").toMatch(/job360_session=;/);
  });

  it.each([429, 500, 503])(
    "a %i from /api/auth/me does NOT bounce to login or delete the cookie",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status }))
      );
      const res = await middleware(protectedRequest());
      // Same service-unavailable path as an outage: cookie kept, marker set.
      const location = new URL(res.headers.get("location")!);
      expect(location.searchParams.get("error")).toBe("service_unavailable");
      expect(res.headers.get("set-cookie") ?? "").not.toMatch(/job360_session=;/);
    }
  );

  it("/stats is protected: no cookie bounces to login", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(new NextRequest("http://localhost:3000/stats"));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("old /people and /settings links only redirect, so they reach their page redirect", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    for (const path of ["/people", "/settings", "/settings/connect"]) {
      const res = await middleware(new NextRequest(`http://localhost:3000${path}`));
      expect(res.headers.get("location")).toBeNull();
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["/outreach/people", "/account", "/connect"])("%s is protected: no cookie bounces to login", async (path) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(new NextRequest(`http://localhost:3000${path}`));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lets a verified session through", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 200 }))
    );
    const res = await middleware(protectedRequest());
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("bounces a protected route with NO cookie without calling the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(
      new NextRequest("http://localhost:3000/profile")
    );
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("public routes never hit the backend at all", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(
      new NextRequest("http://localhost:3000/jobs/123")
    );
    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// F2 hardening — the E2E bypass must fail CLOSED in ANY production, not just on
// Railway. The original guard checked only `!RAILWAY_ENVIRONMENT`, so a non-Railway
// prod deploy (Vercel / Docker / bare VM, where APP_ENV=production is the general
// prod signal) with a stray E2E_TEST_MODE=1 would fully bypass auth. This mirrors
// the backend's own prod-detection: APP_ENV==="production" OR RAILWAY_ENVIRONMENT.
describe("middleware — E2E bypass fails closed in production (F2)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("does NOT bypass on a non-Railway prod deploy (APP_ENV=production)", async () => {
    vi.stubEnv("E2E_TEST_MODE", "1");
    vi.stubEnv("RAILWAY_ENVIRONMENT", ""); // not on Railway
    vi.stubEnv("APP_ENV", "production"); // but still production
    // If the bypass wrongly triggered, no fetch happens and status is 200 with no
    // redirect. Fail-closed means it must VERIFY (call the backend) instead.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(protectedRequest());
    expect(fetchMock).toHaveBeenCalled(); // bypass did NOT short-circuit
    expect(res.status).toBe(307); // 401 → bounce to login
  });

  it("still bypasses in CI/local (no prod signal set)", async () => {
    vi.stubEnv("E2E_TEST_MODE", "1");
    vi.stubEnv("RAILWAY_ENVIRONMENT", "");
    vi.stubEnv("APP_ENV", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(protectedRequest());
    expect(res.status).toBe(200); // trusted, no verify round-trip
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("prod detection is case-insensitive — APP_ENV=Production also fails closed", async () => {
    // The backend's _is_production() lowercases before comparing; a strict
    // === "production" here would treat APP_ENV=Production as NOT prod and
    // re-open the bypass the backend would refuse. Both sides must agree.
    vi.stubEnv("E2E_TEST_MODE", "1");
    vi.stubEnv("RAILWAY_ENVIRONMENT", "");
    vi.stubEnv("APP_ENV", "Production"); // capital P — still production
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(protectedRequest());
    expect(fetchMock).toHaveBeenCalled(); // bypass did NOT short-circuit
    expect(res.status).toBe(307);
  });
});

// ---------------------------------------------------------------------------
// Auth lifecycle (point 3, PR 2 "the gatekeeper"): causes C4, C5, C7, S17.
// ---------------------------------------------------------------------------

const COOKIE = { cookie: "job360_session=some-session-value" };

function locationPath(res: Response): string {
  const loc = new URL(res.headers.get("location")!);
  return loc.pathname + loc.search;
}

describe("middleware — expired-session bounce (C7: S7, S12)", () => {
  beforeEach(() => vi.stubEnv("E2E_TEST_MODE", ""));
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("U7: a rejected cookie keeps the query string and says why", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const res = await middleware(
      new NextRequest("http://localhost:3000/applications/123?tab=cv", { headers: COOKIE })
    );
    expect(res.status).toBe(307);
    expect(locationPath(res)).toBe(
      "/login?reason=expired&next=%2Fapplications%2F123%3Ftab%3Dcv"
    );
  });

  it("no cookie at all: no reason, just the full return path", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(new NextRequest("http://localhost:3000/profile?tab=cv"));
    expect(locationPath(res)).toBe("/login?next=%2Fprofile%3Ftab%3Dcv");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("middleware — verify fetch timeout (S17)", () => {
  beforeEach(() => vi.stubEnv("E2E_TEST_MODE", ""));
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("aborts the /api/auth/me call after 5 s and takes the service-unavailable path", async () => {
    const timedOut = new AbortController();
    timedOut.abort(new DOMException("timed out", "TimeoutError"));
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockReturnValue(timedOut.signal);
    // A backend that never answers: the fetch only settles when its signal aborts.
    const fetchMock = vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal;
          if (!signal) {
            // No signal means it would hang forever (the bug); fail fast instead.
            reject(new Error("verify fetch has no abort signal"));
            return;
          }
          if (signal.aborted) reject(signal.reason);
          signal.addEventListener("abort", () => reject(signal.reason));
        })
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await middleware(
      new NextRequest("http://localhost:3000/profile", { headers: COOKIE })
    );
    expect(timeoutSpy).toHaveBeenCalledWith(5000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(timedOut.signal);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("error")).toBe("service_unavailable");
    expect(res.headers.get("set-cookie") ?? "").not.toMatch(/job360_session=;/);
  });
});

describe("middleware — auth pages for a signed-in user (C4: S4)", () => {
  beforeEach(() => vi.stubEnv("E2E_TEST_MODE", ""));
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("U6: the matcher now runs on /login, /register, /forgot-password and /", () => {
    for (const url of ["/login", "/login?next=%2Fprofile", "/register", "/forgot-password", "/"]) {
      expect(unstable_doesMiddlewareMatch({ config, url }), url).toBe(true);
    }
    for (const url of ["/api/auth/me", "/_next/static/chunk.js", "/favicon.ico"]) {
      expect(unstable_doesMiddlewareMatch({ config, url }), url).toBe(false);
    }
  });

  it("U6: a verified cookie on /login?next=/profile goes to /profile", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(
      new NextRequest("http://localhost:3000/login?next=%2Fprofile", { headers: COOKIE })
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).origin).toBe("http://localhost:3000");
    expect(locationPath(res)).toBe("/profile");
  });

  it.each(["/register", "/forgot-password"])(
    "a verified cookie on %s goes to the default /needs-you",
    async (path) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
      const res = await middleware(new NextRequest(`http://localhost:3000${path}`, { headers: COOKIE }));
      expect(res.status).toBe(307);
      expect(locationPath(res)).toBe("/needs-you");
    }
  );

  it.each([
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    // Dot segments: the URL parser folds these to "//evil.example", which the
    // browser reads as another site once Next makes the Location relative.
    "/..//evil.example",
    "/.//evil.example",
    "/x/..//evil.example",
    "/%2e%2e//evil.example",
  ])(
    "open-redirect safe: next=%s from a signed-in /login lands on /needs-you",
    async (next) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
      const res = await middleware(
        new NextRequest(`http://localhost:3000/login?next=${encodeURIComponent(next)}`, {
          headers: COOKIE,
        })
      );
      expect(res.status).toBe(307);
      const loc = new URL(res.headers.get("location")!);
      expect(loc.origin).toBe("http://localhost:3000");
      expect(loc.pathname + loc.search).toBe("/needs-you");
    }
  );

  it.each(["/login", "/register?x=1", "/forgot-password"])(
    "no redirect loop: next=%s from a signed-in /login lands on /needs-you",
    async (next) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
      const res = await middleware(
        new NextRequest(`http://localhost:3000/login?next=${encodeURIComponent(next)}`, {
          headers: COOKIE,
        })
      );
      expect(res.status).toBe(307);
      expect(locationPath(res)).toBe("/needs-you");
    }
  );

  it("a rejected cookie on /login: delete it and show the page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const res = await middleware(new NextRequest("http://localhost:3000/login", { headers: COOKIE }));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("set-cookie") ?? "").toMatch(/job360_session=;/);
  });

  it("an outage on /login: show the page, keep the cookie, no redirect loop", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const res = await middleware(new NextRequest("http://localhost:3000/login", { headers: COOKIE }));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("set-cookie") ?? "").not.toMatch(/job360_session=;/);
  });

  it("no cookie on /login: show the page without calling the backend", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(new NextRequest("http://localhost:3000/login?next=%2Fprofile"));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("E2E bypass: a present cookie on /login is trusted like the protected branch", async () => {
    vi.stubEnv("E2E_TEST_MODE", "1");
    vi.stubEnv("RAILWAY_ENVIRONMENT", "");
    vi.stubEnv("APP_ENV", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(
      new NextRequest("http://localhost:3000/login?next=%2Fprofile", { headers: COOKIE })
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(res.status).toBe(307);
    expect(locationPath(res)).toBe("/profile");
  });
});

describe("middleware — '/' with a dead cookie (C5: S11)", () => {
  beforeEach(() => vi.stubEnv("E2E_TEST_MODE", ""));
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("a rejected cookie: delete it and hide it from the page so the landing renders", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const res = await middleware(
      new NextRequest("http://localhost:3000/", { headers: { cookie: "job360_session=dead; ph=1" } })
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("set-cookie") ?? "").toMatch(/job360_session=;/);
    // app/page.tsx reads cookies() from the forwarded request headers: the
    // session must be gone there too, or "/" still renders the dashboard.
    expect(res.headers.get("x-middleware-override-headers")).not.toBeNull();
    expect(res.headers.get("x-middleware-request-cookie")).toBe("ph=1");
  });

  it("a valid cookie: unchanged, the dashboard keeps its cookie", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    const res = await middleware(new NextRequest("http://localhost:3000/", { headers: COOKIE }));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-middleware-override-headers")).toBeNull();
  });

  it("an outage: '/' stays public, no redirect, cookie kept", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    const res = await middleware(new NextRequest("http://localhost:3000/", { headers: COOKIE }));
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("no cookie: no backend call, no added latency", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const res = await middleware(new NextRequest("http://localhost:3000/"));
    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
