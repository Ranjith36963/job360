/**
 * Score display on the job detail page.
 *
 * Pins the fix where the detail page led with the KEYWORD match_score even for a
 * judged job. It must now lead with the AI judge fit (the number the dashboard
 * ranks by), show the verdict, and demote the keyword score to a secondary line —
 * and fall back to "Match Score" (keyword) when the job is unjudged.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { JobResponse } from "@/lib/types";
import { JobDetailClient } from "../JobDetailClient";

vi.mock("@/components/jobs/ScoreRadar", () => ({ ScoreRadar: () => <div data-testid="score-radar-mock" /> }));
vi.mock("@/components/jobs/ScoreCounter", () => ({
  ScoreCounter: ({ value }: { value: number }) => <span>{value}</span>,
}));
vi.mock("@/components/jobs/ApplyButton", () => ({ ApplyButton: () => <button type="button">Apply</button> }));
vi.mock("@/components/jobs/DedupGroupViewer", () => ({ DedupGroupViewer: () => <div data-testid="dedup-mock" /> }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/jobs/1",
}));

const BASE: JobResponse = {
  id: 1, title: "AI Engineer", company: "Acme", location: "London, UK", salary: null,
  salary_min_gbp: null, salary_max_gbp: null, salary_period: null,
  match_score: 69, source: "test", date_found: new Date().toISOString(),
  apply_url: "https://acme.com/jobs/1", visa_flag: false, visa_sponsorship: null,
  job_type: "full-time", experience_level: "senior",
  role: 20, skill: 14, seniority_score: 0, experience: 0, credentials: 0,
  location_score: 10, recency: 10, semantic: 0, penalty: 0,
  matched_skills: [], missing_required: [], transferable_skills: [],
  action: null, bucket: "7d", llm_fit_score: null, llm_verdict: null, llm_reason: null,
  seniority: null, workplace_type: null, employment_type: null, industry: null,
  title_canonical: null, years_experience_min: null, required_skills: null,
  posted_at: null, last_seen_at: null, staleness_state: null,
} as unknown as JobResponse;

const getJobMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJob: (...args: unknown[]) => getJobMock(...args),
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("JobDetailClient — primary score display", () => {
  beforeEach(() => getJobMock.mockReset());

  it("judged job leads with the AI fit score + verdict, keyword demoted", async () => {
    getJobMock.mockResolvedValue({
      ...BASE, match_score: 69, llm_fit_score: 88,
      llm_verdict: "Strong overall fit", llm_reason: "skills overlap",
    });
    render(<JobDetailClient jobId={1} />, { wrapper: makeWrapper() });

    await screen.findByText(/AI Fit Score/i);
    expect(screen.getByText("88")).toBeInTheDocument(); // primary = judge score
    expect(screen.queryByText(/^Match Score$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Strong overall fit/)).toBeInTheDocument();
    expect(screen.getByText(/skills overlap/)).toBeInTheDocument();
    // keyword score kept as a secondary recall signal
    expect(screen.getByText(/Keyword match score: 69/)).toBeInTheDocument();
  });

  it("unjudged job leads with the keyword Match Score, no verdict", async () => {
    getJobMock.mockResolvedValue({ ...BASE, match_score: 42, llm_fit_score: null, llm_verdict: null });
    render(<JobDetailClient jobId={1} />, { wrapper: makeWrapper() });

    await screen.findByText(/Match Score/i);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.queryByText(/AI Fit Score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/AI verdict/i)).not.toBeInTheDocument();
  });
});
