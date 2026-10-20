/**
 * M16 — the low-level fetch client must NOT navigate on a 403 email_not_verified.
 *
 * Verifies:
 * 1. request() (exercised via a public GET) throws a typed ApiError whose
 *    `isEmailNotVerified` flag is true.
 * 2. It does NOT touch window.location (no hidden redirect in the fetch client).
 * 3. Subscribers registered via onEmailNotVerified() are notified, so a
 *    top-level handler can own the redirect decision.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getHealth, onEmailNotVerified, submittedBlocked } from "./api";
import { ApiError } from "./api-error";

function mock403(body: unknown) {
  return {
    ok: false,
    status: 403,
    headers: new Headers(),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("request() — 403 email_not_verified handling (M16)", () => {
  let hrefSetter: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // Replace window.location with a spyable stand-in so any attempt by the
    // fetch client to navigate is caught by the test.
    hrefSetter = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        pathname: "/profile",
        get href() {
          return "http://localhost/profile";
        },
        set href(v: string) {
          hrefSetter(v);
        },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("throws a typed ApiError, does NOT navigate, and notifies subscribers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mock403({ detail: "not verified", code: "email_not_verified" }))
    );

    const notified = vi.fn();
    const unsubscribe = onEmailNotVerified(notified);

    let caught: unknown;
    try {
      await getHealth();
    } catch (e) {
      caught = e;
    } finally {
      unsubscribe();
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).isEmailNotVerified).toBe(true);
    // The fetch client itself must never navigate.
    expect(hrefSetter).not.toHaveBeenCalled();
    // The top-level handler is notified instead.
    expect(notified).toHaveBeenCalledTimes(1);
  });

  it("does NOT notify or flag a plain 403 (genuine forbidden)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mock403({ detail: "forbidden", code: "forbidden" }))
    );

    const notified = vi.fn();
    const unsubscribe = onEmailNotVerified(notified);

    let caught: unknown;
    try {
      await getHealth();
    } catch (e) {
      caught = e;
    } finally {
      unsubscribe();
    }

    expect((caught as ApiError).isEmailNotVerified).toBe(false);
    expect(notified).not.toHaveBeenCalled();
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it("unsubscribe stops future notifications", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mock403({ detail: "not verified", code: "email_not_verified" }))
    );

    const notified = vi.fn();
    onEmailNotVerified(notified)(); // subscribe then immediately unsubscribe

    await getHealth().catch(() => {});

    expect(notified).not.toHaveBeenCalled();
  });
});

describe("submittedBlocked (\"I submitted it\")", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts exactly the pasted confirmation to the session-only route", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 201,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ receipt_id: 9, controls: {} }),
      text: async () => "",
    }) as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const res = await submittedBlocked(7, "APP-48213");
    expect(res.receipt_id).toBe(9);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/applications\/7\/blocked\/submitted$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ confirmation: "APP-48213" }));
  });
});
