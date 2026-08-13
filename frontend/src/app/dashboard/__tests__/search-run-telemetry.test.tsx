/**
 * `search_run` must describe the search, and must fire when the search FAILS.
 *
 * PRODUCTION EVIDENCE (PostHog, 2026-08-13). `read-data-schema
 * {"kind":"event_properties","event_name":"search_run"}` returns ONLY
 * posthog-js autocapture defaults ($browser, $pathname, $geoip_*). There is no
 * result count, no duration, no outcome. Eight search_run events exist, from 2
 * persons, and not one of them can be judged good or bad.
 *
 * Two consequences, both load-bearing:
 *
 *  1. The call site sits inside `if (status === "completed")`, so a FAILED
 *     search, a 10-minute timeout, and the "Lost contact with the server while
 *     searching" path (the ~60s event-loop block fixed in PR #123) all emit
 *     NOTHING. The instrument cannot see the failure it most needs to see —
 *     silence means "nobody searched" and "every search died" at once.
 *  2. With no result count, a search that returns zero jobs is
 *     indistinguishable from a good one.
 *
 * Search is the core product action. These tests pin that every terminal
 * outcome is reported and that the event carries enough to judge it.
 */

import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { posthogMock } = vi.hoisted(() => ({
  posthogMock: {
    __loaded: true,
    capture: vi.fn(),
    init: vi.fn(),
    identify: vi.fn(),
    reset: vi.fn(),
    opt_in_capturing: vi.fn(),
    opt_out_capturing: vi.fn(),
  },
}));
vi.mock("posthog-js", () => ({ default: posthogMock }));

const startSearchMock = vi.fn();
const getSearchStatusMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJobs: vi.fn().mockResolvedValue({ jobs: [], total: 0, filters_applied: {} }),
  getStatus: vi.fn().mockResolvedValue({
    jobs_total: 0,
    last_run: null,
    sources_active: 0,
    sources_total: 0,
    profile_exists: true,
  }),
  startSearch: (...args: unknown[]) => startSearchMock(...args),
  getSearchStatus: (...args: unknown[]) => getSearchStatusMock(...args),
}));

import DashboardPage from "../page";

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

/** Find the single search_run capture, or undefined. */
function searchRunCall() {
  return posthogMock.capture.mock.calls.find(([event]) => event === "search_run");
}

describe("dashboard search_run telemetry", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    posthogMock.capture.mockReset();
    startSearchMock.mockReset();
    getSearchStatusMock.mockReset();
    startSearchMock.mockResolvedValue({ run_id: "run-1" });
    sessionStorage.clear();
    // The Profile page's hand-off flag makes the dashboard start a search on
    // mount — a stabler entry point than hunting for the Refresh button.
    sessionStorage.setItem("job360_run_search", "1");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function runAndWaitForCapture() {
    render(<DashboardPage />, { wrapper: makeWrapper() });
    await waitFor(() => expect(startSearchMock).toHaveBeenCalled());
    await vi.advanceTimersByTimeAsync(3500);
    await waitFor(() => expect(searchRunCall()).toBeDefined());
    return searchRunCall()!;
  }

  it("reports how the search went — outcome and result counts", async () => {
    getSearchStatusMock.mockResolvedValue({
      run_id: "run-1",
      status: "completed",
      progress: "Done",
      result: { total_found: 120, new_jobs: 7, sources_queried: 46 },
    });

    const [, props] = await runAndWaitForCapture();

    expect(props).toMatchObject({ outcome: "completed", new_jobs: 7, total_found: 120 });
    expect(typeof (props as { duration_ms?: unknown }).duration_ms).toBe("number");
  });

  it("still reports a search that FAILED (today it emits nothing)", async () => {
    getSearchStatusMock.mockResolvedValue({
      run_id: "run-1",
      status: "failed",
      progress: "Search failed",
      result: null,
    });

    const [, props] = await runAndWaitForCapture();

    expect(props).toMatchObject({ outcome: "failed" });
  });

  it("reports a search that returned nothing as zero, not as absence", async () => {
    getSearchStatusMock.mockResolvedValue({
      run_id: "run-1",
      status: "completed",
      progress: "Done",
      result: { total_found: 0, new_jobs: 0, sources_queried: 46 },
    });

    const [, props] = await runAndWaitForCapture();

    expect(props).toMatchObject({ outcome: "completed", new_jobs: 0, total_found: 0 });
  });
});
