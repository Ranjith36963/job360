/**
 * F2 — empty-window fallback regression test.
 *
 * The dashboard defaults to a 7-day (168h) window. That window can
 * legitimately come back with zero rows (a crawler gap, a brand-new
 * account, a quiet week) even though the user has real matches sitting
 * further back in the catalog. A blank grid reads as "the product is
 * broken", not "nothing new this week".
 *
 * Fix: when the 168h list query succeeds with 0 rows, the page widens the
 * MAIN list query to hours=undefined ("all time") exactly ONCE, and shows a
 * plain notice above the grid. The bucket-counts query (allJobsKey) stays
 * pinned to 168h throughout — its all-zero counts during a fallback are the
 * honest story, not a bug.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { JobFilters, JobResponse } from "@/lib/types";
import DashboardPage from "../page";

// JobCard uses next/navigation for links/routing.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

const getJobsMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJobs: (...args: unknown[]) => getJobsMock(...args),
  // Additive stub: the dashboard also reads GET /api/profile for the
  // "Searching for: …" line. Without a stub the spread `importOriginal`
  // would let the REAL client fire a fetch at jsdom's origin on every test.
  getProfile: vi.fn().mockResolvedValue({ search_titles: [] }),
  getStatus: vi.fn().mockResolvedValue({
    jobs_total: 0,
    last_run: null,
    sources_active: 0,
    sources_total: 0,
    profile_exists: true,
  }),
}));

function makeJob(overrides: Partial<JobResponse> & { id: number; title: string }): JobResponse {
  return {
    company: "Acme Ltd",
    location: "London, UK",
    salary: null,
    match_score: 50,
    source: "test",
    date_found: new Date().toISOString(),
    apply_url: "https://example.com/apply",
    visa_flag: false,
    job_type: "full-time",
    experience_level: "mid",
    role: 0,
    skill: 0,
    seniority_score: 0,
    experience: 0,
    credentials: 0,
    location_score: 0,
    recency: 0,
    semantic: 0,
    penalty: 0,
    matched_skills: [],
    missing_required: [],
    transferable_skills: [],
    action: null,
    bucket: "7d",
    llm_fit_score: null,
    llm_verdict: null,
    llm_reason: null,
    ...overrides,
  };
}

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const NOTICE_TEXT = /no new matches this week/i;

beforeEach(() => {
  getJobsMock.mockReset();
  sessionStorage.clear();
});

describe("DashboardPage — F2 empty-window fallback", () => {
  it("widens to all-time exactly ONCE and shows the notice when the 168h window is empty", async () => {
    // Every call the dashboard makes for the 168h window (both the main
    // list query and the untouched bucket-counts query) comes back empty.
    // Only a call with hours === undefined ("all time") gets real jobs.
    getJobsMock.mockImplementation(async (filters: JobFilters) => {
      if (filters.hours === 168) {
        return { jobs: [], total: 0, total_unfiltered: 0, filters_applied: {} };
      }
      return {
        jobs: [makeJob({ id: 1, title: "Platform Engineer" })],
        total: 1,
        total_unfiltered: 1,
        filters_applied: {},
      };
    });

    render(<DashboardPage />, { wrapper: makeWrapper() });

    // The widened fetch lands and the fallback job renders.
    await waitFor(() => {
      expect(screen.getByText("Platform Engineer")).toBeInTheDocument();
    });

    // The notice is shown above the grid.
    expect(screen.getByText(NOTICE_TEXT)).toBeInTheDocument();

    // Exactly one call ever asked for the widened ("all time") window —
    // proving the fallback fired once and never looped.
    const wideCalls = getJobsMock.mock.calls.filter(
      ([filters]) => (filters as JobFilters).hours === undefined
    );
    expect(wideCalls.length).toBe(1);

    // Give any stray effect/re-render a chance to fire, then confirm the
    // count really is pinned at one — not just "one so far".
    await new Promise((resolve) => setTimeout(resolve, 50));
    const wideCallsAfterSettling = getJobsMock.mock.calls.filter(
      ([filters]) => (filters as JobFilters).hours === undefined
    );
    expect(wideCallsAfterSettling.length).toBe(1);
  });

  it("never widens and shows no notice when the 168h window already has matches", async () => {
    getJobsMock.mockResolvedValue({
      jobs: [makeJob({ id: 2, title: "Data Scientist" })],
      total: 1,
      total_unfiltered: 1,
      filters_applied: {},
    });

    render(<DashboardPage />, { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Data Scientist")).toBeInTheDocument();
    });

    expect(screen.queryByText(NOTICE_TEXT)).not.toBeInTheDocument();

    const wideCalls = getJobsMock.mock.calls.filter(
      ([filters]) => (filters as JobFilters).hours === undefined
    );
    expect(wideCalls.length).toBe(0);
  });
});
