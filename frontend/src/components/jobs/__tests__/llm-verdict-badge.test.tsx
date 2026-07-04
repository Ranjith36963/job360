/**
 * AI verdict badge on JobCard.
 *
 * The LLM judge emits three nullable fields: llm_fit_score, llm_verdict,
 * llm_reason. The card shows a coloured "AI: <verdict>" pill only when both
 * verdict and score are non-null; reason surfaces as the native title tooltip.
 * The fit SCORE itself is shown on the big score badge (which now carries the
 * judge fit when judged — the same number the list is sorted by). This pins the
 * contract so future card refactors can't silently drop the badge or regress the
 * badge back to showing the keyword score under a judged row.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobCard } from "../JobCard";
import type { JobResponse } from "@/lib/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  createPipelineApplication: vi.fn().mockResolvedValue({}),
}));

/** Minimal valid JobResponse fixture (mirrors JobCard.test.tsx baseJob). */
const baseJob: JobResponse = {
  id: 42,
  title: "Senior Software Engineer",
  company: "Acme Corp",
  location: "London, UK",
  salary: null,
  match_score: 78,
  source: "greenhouse",
  date_found: new Date().toISOString(),
  apply_url: "https://example.com/apply",
  visa_flag: false,
  job_type: "Full-time",
  experience_level: "Senior",
  role: 12,
  skill: 18,
  seniority_score: 8,
  experience: 7,
  credentials: 4,
  location_score: 9,
  recency: 8,
  semantic: 15,
  matched_skills: ["Python", "TypeScript"],
  missing_required: [],
  transferable_skills: [],
  action: null,
  bucket: "hot",
};

/** Merge overrides onto baseJob for concise per-test fixtures. */
function jobWith(overrides: Partial<JobResponse>): JobResponse {
  return { ...baseJob, ...overrides };
}

const mockOnAction = vi.fn();

describe("JobCard — AI verdict badge", () => {
  it("shows the AI verdict badge when a verdict exists", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: 93, llm_verdict: "strong fit", llm_reason: "domain match" })}
        onAction={mockOnAction}
      />
    );
    // Badge text: "AI: strong fit · 93"
    expect(screen.getByText(/strong fit/i)).toBeInTheDocument();
    expect(screen.getByText(/93/)).toBeInTheDocument();
  });

  it("renders no AI badge when both fields are null (unjudged)", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: null, llm_verdict: null, llm_reason: null })}
        onAction={mockOnAction}
      />
    );
    expect(screen.queryByText(/AI:/i)).not.toBeInTheDocument();
  });

  it("renders no AI badge when verdict is null even if score present", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: 80, llm_verdict: null, llm_reason: null })}
        onAction={mockOnAction}
      />
    );
    expect(screen.queryByText(/AI:/i)).not.toBeInTheDocument();
  });

  it("renders no AI badge when score is null even if verdict present", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: null, llm_verdict: "good fit", llm_reason: null })}
        onAction={mockOnAction}
      />
    );
    expect(screen.queryByText(/AI:/i)).not.toBeInTheDocument();
  });

  it("uses emerald colour class for score >= 70 (strong fit)", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: 85, llm_verdict: "great match", llm_reason: "skills align" })}
        onAction={mockOnAction}
      />
    );
    const badge = screen.getByText(/great match/i).closest("[class]");
    expect(badge?.className).toMatch(/emerald/);
  });

  it("uses amber colour class for score 40-69 (possible fit)", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: 55, llm_verdict: "possible fit", llm_reason: "some gaps" })}
        onAction={mockOnAction}
      />
    );
    const badge = screen.getByText(/possible fit/i).closest("[class]");
    expect(badge?.className).toMatch(/amber/);
  });

  it("uses red colour class for score < 40 (weak fit)", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: 25, llm_verdict: "weak fit", llm_reason: "missing skills" })}
        onAction={mockOnAction}
      />
    );
    const badge = screen.getByText(/weak fit/i).closest("[class]");
    expect(badge?.className).toMatch(/red/);
  });

  it("attaches llm_reason as title tooltip on the badge", () => {
    render(
      <JobCard
        job={jobWith({ llm_fit_score: 72, llm_verdict: "good fit", llm_reason: "domain expertise matches" })}
        onAction={mockOnAction}
      />
    );
    const badge = screen.getByText(/good fit/i).closest("[title]");
    expect(badge).toHaveAttribute("title", "domain expertise matches");
  });

  it("renders no AI badge when llm fields are absent from job object", () => {
    // baseJob has no llm_* fields — simulates legacy job rows
    render(<JobCard job={baseJob} onAction={mockOnAction} />);
    expect(screen.queryByText(/AI:/i)).not.toBeInTheDocument();
  });

  // ── The actual bug fix: badge number must equal the sort key ────────────────
  it("big badge shows the JUDGE fit score (not the keyword score) when judged", () => {
    // Keyword 30 vs judge 88: the dashboard sorts by the judge, so the badge
    // must show 88 (else the badges look scrambled vs the row order).
    render(
      <JobCard
        job={jobWith({ match_score: 30, llm_fit_score: 88, llm_verdict: "strong fit", llm_reason: "x" })}
        onAction={mockOnAction}
      />
    );
    expect(screen.getByLabelText(/AI fit score: 88/)).toHaveTextContent("88");
    // Keyword score is kept, but as a clearly-labelled secondary chip.
    expect(screen.getByText(/kw 30/)).toBeInTheDocument();
  });

  it("big badge shows the keyword score labelled 'match' when unjudged", () => {
    render(
      <JobCard
        job={jobWith({ match_score: 44, llm_fit_score: null, llm_verdict: null, llm_reason: null })}
        onAction={mockOnAction}
      />
    );
    expect(screen.getByLabelText(/Keyword match score: 44/)).toHaveTextContent("44");
    // No secondary kw chip when there is no judge score to be primary.
    expect(screen.queryByText(/kw 44/)).not.toBeInTheDocument();
  });
});
