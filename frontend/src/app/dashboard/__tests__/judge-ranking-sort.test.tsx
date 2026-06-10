/**
 * Dashboard sorts by the LLM judge's fit score, not raw keyword score.
 *
 * The server orders rows by COALESCE(llm_fit_score, match_score) DESC.
 * The client-side re-sort must mirror that: llm_fit_score when present,
 * match_score otherwise. Without this, a "Poor fit" job with a high keyword
 * score renders above a "Strong Fit" job with a lower keyword score.
 *
 * Fixture order (deliberately wrong — simulates the shared-catalog path
 * which returns by recency / insertion order, not score):
 *   A — Keyword Heavy Intern:    match_score=43, llm_fit_score=20  (judged, poor)
 *   B — Generative AI Engineer:  match_score=34, llm_fit_score=92  (judged, strong)
 *   C — Unjudged Mid Job:        match_score=50, llm_fit_score=null (unjudged)
 *
 * Expected render order: B (92) → C (50) → A (20)
 *   Old sort (match_score only): C(50) → A(43) → B(34)  — B NOT first → RED
 *   New sort (llm_fit_score ?? match_score): B(92) → C(50) → A(20) — B first → GREEN
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { JobResponse } from "@/lib/types";
import DashboardPage from "../page";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// Fixtures — intentionally wrong server order (A before B before C)
const JOB_A = makeJob({
  id: 1,
  title: "Keyword Heavy Intern",
  match_score: 43,
  llm_fit_score: 20,
  llm_verdict: "Poor fit",
});

const JOB_B = makeJob({
  id: 2,
  title: "Generative AI Engineer",
  match_score: 34,
  llm_fit_score: 92,
  llm_verdict: "Strong Fit",
});

const JOB_C = makeJob({
  id: 3,
  title: "Unjudged Mid Job",
  match_score: 50,
  llm_fit_score: null,
  llm_verdict: null,
});

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// JobCard calls useRouter() for navigation — provide a no-op stub so the
// component can render in the jsdom test environment without the App Router.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

const getJobsMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJobs: (...args: unknown[]) => getJobsMock(...args),
  getStatus: vi.fn().mockResolvedValue({
    jobs_total: 3,
    last_run: null,
    sources_active: 0,
    sources_total: 0,
    profile_exists: true,
  }),
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DashboardPage — sorts by LLM judge score, not keyword score", () => {
  beforeEach(() => {
    getJobsMock.mockReset();
    // Return jobs in deliberately wrong order: A(43/20), B(34/92), C(50/null)
    getJobsMock.mockResolvedValue({
      jobs: [JOB_A, JOB_B, JOB_C],
      total: 3,
      filters_applied: {},
    });
    sessionStorage.clear();
  });

  it("renders judged strong-fit job before unjudged and poor-fit jobs", async () => {
    render(<DashboardPage />, { wrapper: makeWrapper() });

    // Wait for the job cards to appear
    await waitFor(() => {
      expect(screen.queryAllByRole("heading", { level: 3 }).length).toBeGreaterThan(0);
    });

    const headings = screen
      .getAllByRole("heading", { level: 3 })
      .map((el) => el.textContent ?? "");

    // B must appear before A and C — the judge's 92 outranks keyword 43 and unjudged 50
    const posA = headings.findIndex((t) => t.includes("Keyword Heavy Intern"));
    const posB = headings.findIndex((t) => t.includes("Generative AI Engineer"));
    const posC = headings.findIndex((t) => t.includes("Unjudged Mid Job"));

    expect(posB).toBeGreaterThanOrEqual(0); // B rendered
    expect(posA).toBeGreaterThanOrEqual(0); // A rendered
    expect(posC).toBeGreaterThanOrEqual(0); // C rendered

    // With old sort (match_score only): C(50) → A(43) → B(34) — B is LAST, not first
    // With new sort (llm_fit_score ?? match_score): B(92) → C(50) → A(20) — B is first
    expect(posB).toBeLessThan(posC); // B before C
    expect(posB).toBeLessThan(posA); // B before A
    expect(posC).toBeLessThan(posA); // C before A (50 > 20)
  });
});
