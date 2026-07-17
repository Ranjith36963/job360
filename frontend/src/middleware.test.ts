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
