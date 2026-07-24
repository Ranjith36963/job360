/**
 * Middleware auth guard — fail-closed on backend outage (docs/fable/03 F4).
 *
 * The guard used to fail OPEN when the backend was unreachable: an unverified
 * session cookie granted access to protected pages. These tests pin the new
 * behaviour: outage → redirect to /login with error=service_unavailable, and —
 * deliberately unlike a 401 bounce — WITHOUT deleting the cookie (the outage is
 * ours; the session may be perfectly valid once the backend recovers).
 */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { middleware } from "./middleware";

function protectedRequest(): NextRequest {
  return new NextRequest("http://localhost:3000/dashboard", {
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
    expect(location.searchParams.get("next")).toBe("/dashboard");
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
      new NextRequest("http://localhost:3000/dashboard")
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
