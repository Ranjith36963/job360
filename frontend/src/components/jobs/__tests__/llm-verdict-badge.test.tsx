/**
 * AI verdict badge on JobCard.
 *
 * The LLM judge emits three nullable fields: llm_fit_score, llm_verdict,
 * llm_reason. The card shows a coloured "AI: <verdict> · <score>" badge
 * only when both verdict and score are non-null; reason surfaces as the
 * native title tooltip. This pins the contract so future card refactors
 * can't silently drop the badge.
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
});
