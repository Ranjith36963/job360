/** me() returns null ONLY on a definite 401; anything else is "unknown". */
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthUnknownError, me } from "../api";

function res(status: number, body: unknown = {}, headers: HeadersInit = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

describe("me()", () => {
  it("returns the user on 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res(200, { id: "u1", email: "a@b.c" })));
    await expect(me()).resolves.toEqual({ id: "u1", email: "a@b.c" });
  });

  it("returns null on a definite 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res(401, { detail: "no" })));
    await expect(me()).resolves.toBeNull();
  });

  it("throws AuthUnknownError on 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res(500, { detail: "boom" })));
    const err = await me().catch((e) => e);
    expect(err).toBeInstanceOf(AuthUnknownError);
    expect(err.status).toBe(500);
  });

  it("throws AuthUnknownError on 429 and exposes Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(res(429, { detail: "slow" }, { "Retry-After": "12" }))
    );
    const err = await me().catch((e) => e);
    expect(err).toBeInstanceOf(AuthUnknownError);
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(12);
  });

  it("throws AuthUnknownError on a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const err = await me().catch((e) => e);
    expect(err).toBeInstanceOf(AuthUnknownError);
    expect(err.status).toBeNull();
  });
});
