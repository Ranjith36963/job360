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
    // EVERY spy, not the two this file happened to assert on first. `init` and
    // `capture` were cleared and `opt_out_capturing` / `opt_in_capturing` /
    // `reset` were not, so a call from an earlier test satisfied a later test's
    // `toHaveBeenCalled()`. Caught by mutation-testing these cases: with the
    // consent bug deliberately restored, three of them still passed — on
    // evidence left behind by their predecessor. A test that can be satisfied
    // by another test is not a test.
    posthogMock.init.mockClear();
    posthogMock.capture.mockClear();
    posthogMock.opt_in_capturing.mockClear();
    posthogMock.opt_out_capturing.mockClear();
    posthogMock.reset.mockClear();
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
  // ── Consent withdrawal: the ORDER of the two calls is the whole rule ──────
  //
  // `reset()` clears the SDK's consent state. So `opt_out_capturing()` followed
  // by `reset()` UNDOES the opt-out, and with `opt_out_capturing_by_default:
  // false` the SDK is free to capture again while the application still reads
  // "declined". Tracking a user who has withdrawn consent is the failure this
  // guards, and both calls happening is not enough to prevent it — only their
  // order is. (CodeRabbit, PR #387.)
  it("opts out AFTER resetting, so the opt-out is not cleared again", async () => {
    posthogMock.__loaded = true; // loaded earlier in this session
    window.localStorage.setItem(CONSENT_KEY, "declined");

    render(
      <PostHogProviderWrapper>
        <div>child</div>
      </PostHogProviderWrapper>
    );

    await waitFor(() => expect(posthogMock.opt_out_capturing).toHaveBeenCalled());
    const resetAt = calls.indexOf("reset");
    const optOutAt = calls.indexOf("opt_out");
    expect(resetAt).toBeGreaterThanOrEqual(0);
    expect(optOutAt).toBeGreaterThanOrEqual(0);
    expect(optOutAt).toBeGreaterThan(resetAt);
  });

  // ── EVERY non-accepted value must opt out, not just the string "declined" ──
  //
  // The gate above already means "not accepted"; re-narrowing it underneath to
  // one exact string left capture RUNNING for any other value — a withdrawal, a
  // cleared choice, a value added later. A consent check must never be narrower
  // than the consent rule it implements.
  it.each(["withdrawn", "unknown-future-value", ""])(
    "opts out for a non-accepted consent value: %s",
    async (value) => {
      posthogMock.__loaded = true;
      window.localStorage.setItem(CONSENT_KEY, value);

      render(
        <PostHogProviderWrapper>
          <div>child</div>
        </PostHogProviderWrapper>
      );

      await waitFor(() => expect(posthogMock.opt_out_capturing).toHaveBeenCalled());
    }
  );

  // ── accepted -> declined -> accepted, the transition CodeRabbit asked for ──
  it("re-enables capture when consent is granted again after a decline", async () => {
    posthogMock.__loaded = true;
    window.localStorage.setItem(CONSENT_KEY, "declined");

    const { rerender } = render(
      <PostHogProviderWrapper>
        <div>child</div>
      </PostHogProviderWrapper>
    );
    await waitFor(() => expect(posthogMock.opt_out_capturing).toHaveBeenCalled());

    // Re-accept: the store is external, so change it and notify like the real
    // consent banner does.
    window.localStorage.setItem(CONSENT_KEY, "accepted");
    window.dispatchEvent(new Event("job360:consent"));
    rerender(
      <PostHogProviderWrapper>
        <div>child</div>
      </PostHogProviderWrapper>
    );

    await waitFor(() => expect(posthogMock.opt_in_capturing).toHaveBeenCalled());
    // ...and the opt-IN must be the LAST word, or the session stays silent.
    expect(calls.lastIndexOf("opt_in")).toBeGreaterThan(calls.lastIndexOf("opt_out"));
  });
});
