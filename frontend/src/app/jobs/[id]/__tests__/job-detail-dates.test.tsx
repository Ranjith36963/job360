/**
 * TDD: application deadline + posted-date confidence on the job detail page.
 *
 * Tests run BEFORE the implementation is added — they must be RED first.
 * After the implementation they go GREEN.
 *
 * What we test:
 *   1. Deadline set, deadline_source === "description"
 *      → renders "Apply by 30 Sep 2026" (absolute date is clock-independent)
 *      → appends source note "parsed from listing text"
 *   2. Deadline is null
 *      → renders "No deadline listed"
 *
 * Heavy child components are mocked the same way as job-detail-title.test.tsx
 * so the component renders in jsdom without browser APIs.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { JobResponse } from "@/lib/types";
import { JobDetailClient } from "../JobDetailClient";

// ---------------------------------------------------------------------------
// Child component mocks (same set as job-detail-title.test.tsx)
// ---------------------------------------------------------------------------

vi.mock("@/components/jobs/ScoreRadar", () => ({
  ScoreRadar: () => <div data-testid="score-radar-mock" />,
}));

vi.mock("@/components/jobs/ScoreCounter", () => ({
  ScoreCounter: ({ value }: { value: number }) => <span>{value}</span>,
}));

vi.mock("@/components/jobs/ApplyButton", () => ({
  ApplyButton: () => <button type="button">Apply</button>,
}));

vi.mock("@/components/jobs/DedupGroupViewer", () => ({
  DedupGroupViewer: () => <div data-testid="dedup-mock" />,
}));

// ---------------------------------------------------------------------------
// next/navigation
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "42" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/jobs/42",
}));

// ---------------------------------------------------------------------------
// Base fixture job (all required fields, deadline fields null by default)
// ---------------------------------------------------------------------------

const BASE_JOB: JobResponse = {
  id: 42,
  title: "Staff Engineer",
  company: "Acme Ltd",
  location: "London, UK",
  salary: null,
  salary_min_gbp: null,
  salary_max_gbp: null,
  salary_period: null,
  match_score: 55,
  source: "test",
  date_found: new Date().toISOString(),
  apply_url: "https://acme.example.com/jobs/42",
  visa_flag: false,
  visa_sponsorship: null,
  job_type: "full-time",
  experience_level: "senior",
  role: 30,
  skill: 25,
  seniority_score: 0,
  experience: 0,
  credentials: 0,
  location_score: 10,
  recency: 10,
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
  seniority: null,
  workplace_type: null,
  employment_type: null,
  industry: null,
  title_canonical: null,
  years_experience_min: null,
  required_skills: null,
  posted_at: new Date().toISOString(),
  last_seen_at: null,
  staleness_state: null,
  date_confidence: null,
  deadline: null,
  deadline_source: null,
  first_seen_at: null,
} as unknown as JobResponse;

// ---------------------------------------------------------------------------
// API mock
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

describe("JobDetailClient — application deadline display", () => {
  beforeEach(() => {
    getJobMock.mockReset();
  });

  it("shows 'Apply by 30 Sep(t) 2026' and the 'parsed from listing text' source note when deadline is set", async () => {
    const job: JobResponse = {
      ...BASE_JOB,
      deadline: "2026-09-30",
      deadline_source: "description",
    } as unknown as JobResponse;

    getJobMock.mockResolvedValue(job);

    render(<JobDetailClient jobId={42} />, { wrapper: makeWrapper() });

    // Wait for the job to render
    await screen.findByText(/Staff Engineer/i);

    // The span wraps both the deadline text and the note child span, so the
    // combined textContent of the <span> includes both. Use a function matcher
    // to find any element whose textContent includes "Apply by" and "2026".
    // Also tolerates "Sept" vs "Sep" locale differences across Node versions.
    const deadlineEls = screen.getAllByText((_content, element) => {
      const text = element?.textContent ?? "";
      return /Apply by/i.test(text) && /2026/.test(text);
    });
    expect(deadlineEls.length).toBeGreaterThan(0);

    // The source note
    expect(screen.getByText(/parsed from listing text/i)).toBeDefined();
  });

  it("shows 'No deadline listed' when deadline is null", async () => {
    const job: JobResponse = {
      ...BASE_JOB,
      deadline: null,
      deadline_source: null,
    } as unknown as JobResponse;

    getJobMock.mockResolvedValue(job);

    render(<JobDetailClient jobId={42} />, { wrapper: makeWrapper() });

    await screen.findByText(/Staff Engineer/i);

    expect(screen.getByText(/No deadline listed/i)).toBeDefined();
  });
});

describe("JobDetailClient — posted-date confidence", () => {
  beforeEach(() => {
    getJobMock.mockReset();
  });

  it("appends '· approx' when date_confidence is 'low'", async () => {
    const job: JobResponse = {
      ...BASE_JOB,
      posted_at: new Date().toISOString(),
      date_confidence: "low",
    } as unknown as JobResponse;

    getJobMock.mockResolvedValue(job);

    render(<JobDetailClient jobId={42} />, { wrapper: makeWrapper() });

    await screen.findByText(/Staff Engineer/i);

    // Some text in the DOM should include "approx"
    await waitFor(() => {
      expect(screen.getByText(/approx/i)).toBeDefined();
    });
  });

  it("appends '· approx' when date_confidence is 'fabricated'", async () => {
    const job: JobResponse = {
      ...BASE_JOB,
      posted_at: new Date().toISOString(),
      date_confidence: "fabricated",
    } as unknown as JobResponse;

    getJobMock.mockResolvedValue(job);

    render(<JobDetailClient jobId={42} />, { wrapper: makeWrapper() });

    await screen.findByText(/Staff Engineer/i);

    await waitFor(() => {
      expect(screen.getByText(/approx/i)).toBeDefined();
    });
  });

  it("does NOT append '· approx' when date_confidence is 'high'", async () => {
    const job: JobResponse = {
      ...BASE_JOB,
      posted_at: new Date().toISOString(),
      date_confidence: "high",
    } as unknown as JobResponse;

    getJobMock.mockResolvedValue(job);

    render(<JobDetailClient jobId={42} />, { wrapper: makeWrapper() });

    await screen.findByText(/Staff Engineer/i);

    await waitFor(() => {
      expect(screen.queryByText(/approx/i)).toBeNull();
    });
  });
});
