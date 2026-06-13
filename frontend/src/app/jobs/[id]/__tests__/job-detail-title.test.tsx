/**
 * TDD: client-side document.title sync on soft navigation.
 *
 * Bug: after in-app navigation to /jobs/[id], the browser tab title shows the
 * WRONG job because Next.js 16's client-side router does not re-run
 * generateMetadata (SSR-only). document.title is left at the previous page's
 * value.
 *
 * Fix: a useEffect in JobDetailClient sets document.title once `job` loads,
 * matching the server generateMetadata format exactly:
 *   `${job.title} at ${job.company} — Job360`
 *
 * This test:
 *   1. Mocks `@/lib/api` so getJob resolves immediately with a known job.
 *   2. Renders <JobDetailClient jobId={1} />.
 *   3. Waits for the job title to appear in the DOM.
 *   4. Asserts document.title is the correct string.
 *
 * WITHOUT the useEffect the title won't be set → RED.
 * WITH the useEffect it matches exactly → GREEN.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { JobResponse } from "@/lib/types";
import { JobDetailClient } from "../JobDetailClient";

// ---------------------------------------------------------------------------
// Child components that use browser APIs not available in jsdom
// ---------------------------------------------------------------------------

// ScoreRadar uses Recharts (SVG + ResizeObserver) — mock to avoid jsdom errors.
vi.mock("@/components/jobs/ScoreRadar", () => ({
  ScoreRadar: () => <div data-testid="score-radar-mock" />,
}));

// ScoreCounter uses a counting animation that is fine in jsdom, but mock for
// simplicity to avoid any canvas/animation edge cases.
vi.mock("@/components/jobs/ScoreCounter", () => ({
  ScoreCounter: ({ value }: { value: number }) => <span>{value}</span>,
}));

// ApplyButton may trigger navigation on mount — keep it inert.
vi.mock("@/components/jobs/ApplyButton", () => ({
  ApplyButton: () => <button type="button">Apply</button>,
}));

// DedupGroupViewer makes an API call on mount — stub it.
vi.mock("@/components/jobs/DedupGroupViewer", () => ({
  DedupGroupViewer: () => <div data-testid="dedup-mock" />,
}));

// ---------------------------------------------------------------------------
// next/navigation — JobDetailClient calls useParams()
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/jobs/1",
}));

// ---------------------------------------------------------------------------
// Fixture job
// ---------------------------------------------------------------------------

const FIXTURE_JOB: JobResponse = {
  id: 1,
  title: "Senior Specialist Solutions Engineer (AI/ML)",
  company: "Databricks",
  location: "London, UK",
  salary: null,
  salary_min_gbp: null,
  salary_max_gbp: null,
  salary_period: null,
  match_score: 34,
  source: "test",
  date_found: new Date().toISOString(),
  apply_url: "https://databricks.com/jobs/1",
  visa_flag: false,
  visa_sponsorship: null,
  job_type: "full-time",
  experience_level: "senior",
  role: 20,
  skill: 14,
  seniority_score: 0,
  experience: 0,
  credentials: 0,
  location_score: 10,
  recency: 10,
  semantic: 0,
  penalty: 0,
  matched_skills: ["Python", "ML"],
  missing_required: [],
  transferable_skills: [],
  action: null,
  bucket: "7d",
  llm_fit_score: null,
  llm_verdict: null,
  llm_reason: null,
  // Enrichment fields (optional / nullable)
  seniority: null,
  workplace_type: null,
  employment_type: null,
  industry: null,
  title_canonical: null,
  years_experience_min: null,
  required_skills: null,
  posted_at: null,
  last_seen_at: null,
  staleness_state: null,
} as unknown as JobResponse; // cast for any schema fields we haven't filled

// ---------------------------------------------------------------------------
// API mock — getJob resolves with the fixture
// ---------------------------------------------------------------------------

const getJobMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJob: (...args: unknown[]) => getJobMock(...args),
}));

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("JobDetailClient — document.title sync on soft navigation", () => {
  const ORIGINAL_TITLE = document.title;

  beforeEach(() => {
    getJobMock.mockReset();
    getJobMock.mockResolvedValue(FIXTURE_JOB);
    document.title = "Lead ML Engineer at Harnham — Job360"; // simulate stale title from previous page
  });

  afterEach(() => {
    document.title = ORIGINAL_TITLE;
  });

  it("sets document.title to match the server generateMetadata format once the job loads", async () => {
    render(<JobDetailClient jobId={1} />, { wrapper: makeWrapper() });

    // Wait for the job to be rendered (h1 contains the title)
    await screen.findByText(/Senior Specialist Solutions Engineer/i);

    // The useEffect must have fired by now
    expect(document.title).toBe(
      "Senior Specialist Solutions Engineer (AI/ML) at Databricks — Job360"
    );
  });

  it("does NOT overwrite the title while the job is still loading", async () => {
    // Never resolve — stays in loading state
    getJobMock.mockReturnValue(new Promise(() => {}));

    render(<JobDetailClient jobId={1} />, { wrapper: makeWrapper() });

    // Loading skeleton is showing; title must be left untouched
    await waitFor(() => {
      expect(document.title).toBe("Lead ML Engineer at Harnham — Job360");
    });
  });
});
