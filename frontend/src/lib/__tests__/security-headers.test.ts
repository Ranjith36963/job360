/**
 * M15 — no security headers were set at all before this. Pins the minimum
 * required set (nosniff, frame-deny, referrer policy) plus a CSP that stays
 * permissive enough for Sentry (direct-to-ingest) and PostHog (EU/US cloud).
 */

import { describe, it, expect } from "vitest";
import { SECURITY_HEADERS, CONTENT_SECURITY_POLICY } from "../security-headers";

function headerValue(key: string): string | undefined {
  return SECURITY_HEADERS.find((h) => h.key === key)?.value;
}

describe("SECURITY_HEADERS (M15)", () => {
  it("sets X-Content-Type-Options: nosniff", () => {
    expect(headerValue("X-Content-Type-Options")).toBe("nosniff");
  });

  it("sets X-Frame-Options: DENY", () => {
    expect(headerValue("X-Frame-Options")).toBe("DENY");
  });

  it("sets a strict Referrer-Policy", () => {
    expect(headerValue("Referrer-Policy")).toBe("strict-origin-when-cross-origin");
  });

  it("includes a Content-Security-Policy with frame-ancestors 'none'", () => {
    expect(CONTENT_SECURITY_POLICY).toMatch(/frame-ancestors 'none'/);
  });

  it("CSP connect-src allows Sentry ingest domains", () => {
    expect(CONTENT_SECURITY_POLICY).toMatch(/sentry\.io/);
  });

  it("CSP connect-src allows PostHog EU + US cloud hosts", () => {
    expect(CONTENT_SECURITY_POLICY).toMatch(/eu\.i\.posthog\.com/);
    expect(CONTENT_SECURITY_POLICY).toMatch(/us\.i\.posthog\.com/);
  });

  it("CSP worker-src allows same-origin blob workers (PostHog)", () => {
    // Without an explicit worker-src, the browser falls back to script-src,
    // which has no blob: — so PostHog's blob Web Worker was blocked with a
    // console error on EVERY page load. Allow self + blob: for workers only
    // (NOT in script-src — page scripts stay locked to 'self').
    expect(CONTENT_SECURITY_POLICY).toMatch(/worker-src 'self' blob:/);
  });
});
