/**
 * M14 — middleware must fail CLOSED when the backend auth check
 * (`/api/auth/me`) is unreachable, instead of serving the protected shell.
 *
 * Before the fix, a fetch error in the try/catch returned NextResponse.next(),
 * which let ANY request with a `job360_session` cookie through to a protected
 * route while the backend was down/unreachable — cookie validity was never
 * actually checked in that path.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";

const originalFetch = global.fetch;

beforeEach(() => {
  vi.resetAllMocks();
});

afterEach(() => {
  global.fetch = originalFetch;
});

function makeRequest(path: string, cookie?: string) {
  return new NextRequest(`http://localhost:3000${path}`, {
    headers: cookie ? { cookie } : {},
  });
}

describe("middleware — M14 fail-closed on backend-unreachable", () => {
  it("redirects to /login (not next()) when the auth-check fetch throws", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));

    const req = makeRequest("/dashboard", "job360_session=some-valid-looking-value");
    const res = await middleware(req);

    expect(res.status).toBe(307);
    const location = res.headers.get("location");
    expect(location).toContain("/login");
  });

  it("does NOT delete the session cookie on a network failure (transient, not invalid)", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED"));

    const req = makeRequest("/dashboard", "job360_session=some-valid-looking-value");
    const res = await middleware(req);

    // bounceToLogin() (the "cookie is known-bad" path) deletes the cookie by
    // appending a Set-Cookie header with Max-Age=0. The network-failure path
    // must NOT do that.
    const setCookie = res.headers.get("set-cookie");
    expect(setCookie ?? "").not.toMatch(/job360_session=;/);
  });

  it("still bounces to login and clears the cookie for a genuinely invalid session (backend reachable, 401)", async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));

    const req = makeRequest("/dashboard", "job360_session=stale-cookie");
    const res = await middleware(req);

    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });

  it("passes through when the backend confirms the session is valid", async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));

    const req = makeRequest("/dashboard", "job360_session=good-cookie");
    const res = await middleware(req);

    // NextResponse.next() results in a 200 passthrough with no redirect.
    expect(res.status).toBe(200);
    expect(res.headers.get("location")).toBeNull();
  });

  it("bounces unauthenticated requests (no cookie) straight to login without calling fetch", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock;

    const req = makeRequest("/dashboard");
    const res = await middleware(req);

    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not gate public paths like /jobs", async () => {
    const fetchMock = vi.fn();
    global.fetch = fetchMock;

    const req = makeRequest("/jobs/123");
    const res = await middleware(req);

    expect(res.status).toBe(200);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
