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
    expect(CONTENT_SECURITY_POLICY).toContain("frame-ancestors 'none'");
  });

  // These assert that a SUBSTRING is present in the CSP header, so `toContain`
  // says exactly that. They used `toMatch` with unanchored regexes, which CodeQL
  // flags as `js/regex/missing-regexp-anchor` (HIGH) -- a rule aimed at host
  // VALIDATION, where an unanchored pattern lets `evil-sentry.io.attacker.com`
  // through. Here nothing is being validated; a header string is being searched.
  //
  // Dismissing it as a false positive would have been defensible and worse: the
  // alert blocked PR #258 for a file that PR does not touch, and a security check
  // that is permanently red for a known-benign reason is one nobody reads. The
  // assertion is clearer this way regardless.
  it("CSP connect-src allows Sentry ingest domains", () => {
    expect(CONTENT_SECURITY_POLICY).toContain("sentry.io");
  });

  it("CSP connect-src allows PostHog EU + US cloud hosts", () => {
    expect(CONTENT_SECURITY_POLICY).toContain("eu.i.posthog.com");
    expect(CONTENT_SECURITY_POLICY).toContain("us.i.posthog.com");
  });

  it("CSP worker-src allows same-origin blob workers (PostHog)", () => {
    // Without an explicit worker-src, the browser falls back to script-src,
    // which has no blob: — so PostHog's blob Web Worker was blocked with a
    // console error on EVERY page load. Allow self + blob: for workers only
    // (NOT in script-src — page scripts stay locked to 'self').
    expect(CONTENT_SECURITY_POLICY).toContain("worker-src 'self' blob:");
  });
});
