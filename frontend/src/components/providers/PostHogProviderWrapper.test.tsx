/**
 * $pageview must be captured AFTER posthog.init(), never before.
 *
 * WHY THIS TEST EXISTS (production evidence, 2026-08-13).
 * PostHog received $pageleave and $set events from /profile on four
 * consecutive days (2026-08-10 → 2026-08-13) and ZERO $pageview events; the
 * last $pageview of any kind was 2026-08-09T08:29:48Z. $pageleave is emitted
 * by posthog-js itself (capture_pageleave: true) so it can only fire after
 * init succeeded — proving the SDK was loaded and the wire to PostHog was
 * fine. The hand-rolled half was the broken half.
 *
 * The mechanism is React effect ordering. PageViewTracker is a CHILD of
 * PostHogProviderWrapper, and React runs child effects before parent effects
 * (bottom-up). So on a fresh page load the child called
 * posthog.capture("$pageview") before the parent called posthog.init() — and a
 * capture before init is a silent no-op in posthog-js. The child's deps are
 * [pathname, searchParams], neither of which changes afterwards, so the effect
 * never re-ran and that page load produced no pageview at all. Only a
 * client-side route change (which re-runs the effect after init) ever produced
 * one, which is exactly why pageviews vanished when the only active person
 * stopped navigating and just opened /profile directly.
 *
 * Consequence: every funnel whose first step is $pageview under-counts, and
 * the "25 landing visitors → 2 register views" number is measured with a
 * broken ruler.
 */

import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CONSENT_KEY } from "@/lib/consent";

// ---------------------------------------------------------------------------
// Mocks — hoisted before module evaluation
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  usePathname: () => "/profile",
  useSearchParams: () => new URLSearchParams(""),
}));

// Order tape: every posthog call appends its name, so the test can assert the
// SEQUENCE and not merely that both happened. vi.hoisted because vi.mock's
// factory is lifted above the module's own top-level declarations.
const { calls, posthogMock } = vi.hoisted(() => {
  const tape: string[] = [];
  const mock = {
    __loaded: false,
    init: vi.fn(() => {
      tape.push("init");
      mock.__loaded = true;
    }),
    capture: vi.fn((event: string) => {
      tape.push(`capture:${event}`);
    }),
    opt_in_capturing: vi.fn(() => tape.push("opt_in")),
    opt_out_capturing: vi.fn(() => tape.push("opt_out")),
    reset: vi.fn(() => tape.push("reset")),
  };
  return { calls: tape, posthogMock: mock };
});

vi.mock("posthog-js", () => ({ default: posthogMock }));

vi.mock("posthog-js/react", () => ({
  PostHogProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { PostHogProviderWrapper } from "./PostHogProviderWrapper";

describe("PostHogProviderWrapper — $pageview ordering", () => {
  beforeEach(() => {
    calls.length = 0;
    posthogMock.__loaded = false;
    posthogMock.init.mockClear();
    posthogMock.capture.mockClear();
    window.localStorage.clear();
    vi.stubEnv("NEXT_PUBLIC_POSTHOG_KEY", "phc_test_key");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("captures $pageview on a first page load, and only after init", async () => {
    window.localStorage.setItem(CONSENT_KEY, "accepted");

    render(
      <PostHogProviderWrapper>
        <div>child</div>
      </PostHogProviderWrapper>
    );

    await waitFor(() => expect(posthogMock.init).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(posthogMock.capture).toHaveBeenCalledWith("$pageview", expect.anything())
    );

    const initAt = calls.indexOf("init");
    const pageviewAt = calls.indexOf("capture:$pageview");
    expect(initAt).toBeGreaterThanOrEqual(0);
    expect(pageviewAt).toBeGreaterThanOrEqual(0);
    // The whole bug in one assertion: a capture that lands before init is lost.
    expect(pageviewAt).toBeGreaterThan(initAt);
  });

  it("captures nothing at all when consent is not accepted", async () => {
    window.localStorage.setItem(CONSENT_KEY, "declined");

    render(
      <PostHogProviderWrapper>
        <div>child</div>
      </PostHogProviderWrapper>
    );

    // Give any effect a chance to run before asserting absence.
    await waitFor(() => expect(posthogMock.init).not.toHaveBeenCalled());
    expect(posthogMock.capture).not.toHaveBeenCalled();
  });
});
