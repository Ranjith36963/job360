import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobCard } from "./JobCard";
import type { JobResponse } from "@/lib/types";

// Mock Next.js router and pipeline API
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  createPipelineApplication: vi.fn().mockResolvedValue({}),
}));

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
  matched_skills: ["Python", "TypeScript", "React"],
  missing_required: ["Rust"],
  transferable_skills: ["Go"],
  action: null,
  bucket: "hot",
};

// Helper to build a minimal valid job fixture on top of baseJob.
function makeJob(overrides: Partial<JobResponse>): JobResponse {
  return { ...baseJob, ...overrides };
}

describe("JobCard", () => {
  const mockOnAction = vi.fn();

  it("renders job title and company", () => {
    render(<JobCard job={baseJob} onAction={mockOnAction} />);
    expect(screen.getByText("Senior Software Engineer")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
  });

  it("renders match score", () => {
    render(<JobCard job={baseJob} onAction={mockOnAction} />);
    expect(screen.getByText("78")).toBeInTheDocument();
  });

  it("value-presence: renders structured salary range when salary_min/max present", () => {
    const jobWithSalary = {
      ...baseJob,
      salary_min_gbp: 60000,
      salary_max_gbp: 90000,
    };
    render(<JobCard job={jobWithSalary} onAction={mockOnAction} />);
    expect(screen.getByText("£60k–£90k")).toBeInTheDocument();
  });

  it("value-presence: renders seniority pill when seniority field present", () => {
    const jobWithSeniority = { ...baseJob, seniority: "senior" };
    render(<JobCard job={jobWithSeniority} onAction={mockOnAction} />);
    expect(screen.getByText("senior")).toBeInTheDocument();
  });

  it("value-presence: renders workplace type badge when present", () => {
    const jobWithWorkplace = { ...baseJob, workplace_type: "hybrid" };
    render(<JobCard job={jobWithWorkplace} onAction={mockOnAction} />);
    expect(screen.getByText("hybrid")).toBeInTheDocument();
  });

  it("null guard: renders without crashing when matched_skills is empty", () => {
    const jobNoSkills = { ...baseJob, matched_skills: [], missing_required: [], transferable_skills: [] };
    expect(() => render(<JobCard job={jobNoSkills} onAction={mockOnAction} />)).not.toThrow();
  });

  it("calls onAction with 'liked' when Like button clicked", async () => {
    render(<JobCard job={baseJob} onAction={mockOnAction} />);
    await userEvent.click(screen.getByRole("button", { name: /like this job/i }));
    expect(mockOnAction).toHaveBeenCalledWith(42, "liked");
  });

  // ── B-2 — salary point estimate ────────────────────────────────────────────
  it("formats salary as point estimate when min == max (B-2)", () => {
    const jobPoint = { ...baseJob, salary_min_gbp: 83284, salary_max_gbp: 83284 };
    render(<JobCard job={jobPoint} onAction={mockOnAction} />);
    expect(screen.getByText("£83k")).toBeInTheDocument();
    // No fake range marker rendered.
    expect(screen.queryByText(/£83k.*–.*£83k/)).not.toBeInTheDocument();
  });

  it("still renders a range when min != max (B-2 regression guard)", () => {
    const jobRange = { ...baseJob, salary_min_gbp: 60000, salary_max_gbp: 90000 };
    render(<JobCard job={jobRange} onAction={mockOnAction} />);
    expect(screen.getByText("£60k–£90k")).toBeInTheDocument();
  });

  // ── B-3 — source label tooltip ─────────────────────────────────────────────
  it("source label has native title tooltip (B-3)", () => {
    render(<JobCard job={baseJob} onAction={mockOnAction} />);
    expect(screen.getByText("greenhouse")).toHaveAttribute("title", "greenhouse");
  });

  // ── B-6 / B-7 — title / company / location tooltips ────────────────────────
  it("title / company / location all have native title tooltips (B-6/B-7)", () => {
    const longJob = {
      ...baseJob,
      title: "Senior AI Engineer (Enterprise AI Platform): Foundations Team",
      company: "Adria Solutions Limited UK",
      location: "Manchester, Greater Manchester, UK",
    };
    render(<JobCard job={longJob} onAction={mockOnAction} />);
    expect(screen.getByText(longJob.title)).toHaveAttribute("title", longJob.title);
    expect(screen.getByText(longJob.company)).toHaveAttribute("title", longJob.company);
    expect(screen.getByText(longJob.location)).toHaveAttribute("title", longJob.location);
  });

  // ── Date / deadline tests ──────────────────────────────────────────────────

  // D-1: renders "Posted X ago" when posted_at is set, not "Seen"
  it("D-1: shows 'Posted' label (not 'Seen') when posted_at is provided", () => {
    const job = makeJob({
      posted_at: new Date(Date.now() - 2 * 24 * 3600 * 1000).toISOString(), // 2d ago
      date_confidence: "high",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText(/^Posted /)).toBeInTheDocument();
    // Must NOT say "Seen" when posted_at is present
    expect(screen.queryByText(/^Seen /)).not.toBeInTheDocument();
  });

  // D-2: low/fabricated confidence renders "~" prefix (approx marker)
  it("D-2: prefixes '~' when date_confidence is 'low'", () => {
    const job = makeJob({
      posted_at: new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString(),
      date_confidence: "low",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    // The posted text should begin with "~Posted"
    expect(screen.getByText(/^~Posted /)).toBeInTheDocument();
  });

  // D-3: when posted_at is null, falls back to "Seen …" — never "Posted"
  it("D-3: falls back to 'Seen' label (never 'Posted') when posted_at is null", () => {
    const job = makeJob({
      posted_at: null,
      date_confidence: null,
      last_seen_at: new Date(Date.now() - 5 * 24 * 3600 * 1000).toISOString(),
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText(/^Seen /)).toBeInTheDocument();
    expect(screen.queryByText(/^Posted /)).not.toBeInTheDocument();
    expect(screen.queryByText(/^~Posted /)).not.toBeInTheDocument();
  });

  // D-4: renders "Apply by 18 Jun 2026" when deadline is set (clock-independent assertion)
  it("D-4: renders formatted deadline date when deadline is present", () => {
    const job = makeJob({ deadline: "2026-06-18", deadline_source: "listing" });
    render(<JobCard job={job} onAction={mockOnAction} />);
    // The deadline renders inside a Base UI Tooltip trigger whose accessible
    // name is set via aria-label ("Apply by 18 Jun 2026"); the visible text is
    // split across the icon + text nodes, so query by the accessible label
    // (a11y-correct and reliable in jsdom). "18 Jun 2026" is clock-independent.
    expect(screen.getByLabelText(/Apply by 18 Jun 2026/)).toBeInTheDocument();
  });

  // D-5: renders "No deadline listed" when deadline is null
  it("D-5: renders 'No deadline listed' when deadline is null", () => {
    const job = makeJob({ deadline: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("No deadline listed")).toBeInTheDocument();
  });

  // ── F3 — staleness badge vocabulary (real lowercase DB values) ─────────────
  // Prod (2026-08-15, all 10,257 jobs) only ever writes these four lowercase
  // strings (ghost_detection.py). The badge used to compare against uppercase
  // literals that never existed, so every one of these cases rendered wrong.

  it("F3: shows NO staleness badge for 'active'", () => {
    const job = makeJob({ staleness_state: "active" });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.queryByText(/possibly stale/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/may be filled/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/expired/i)).not.toBeInTheDocument();
    expect(screen.queryByText("active")).not.toBeInTheDocument();
  });

  it("F3: shows amber 'Possibly stale' badge for 'possibly_stale'", () => {
    const job = makeJob({ staleness_state: "possibly_stale" });
    render(<JobCard job={job} onAction={mockOnAction} />);
    const badge = screen.getByText("Possibly stale");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveClass("text-amber-400");
    // The raw DB string must never leak into the UI.
    expect(screen.queryByText("possibly_stale")).not.toBeInTheDocument();
  });

  it("F3: shows amber 'May be filled' badge for 'likely_stale'", () => {
    const job = makeJob({ staleness_state: "likely_stale" });
    render(<JobCard job={job} onAction={mockOnAction} />);
    const badge = screen.getByText("May be filled");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveClass("text-amber-400");
    expect(screen.queryByText("likely_stale")).not.toBeInTheDocument();
  });

  it("F3: shows red 'Expired' badge for 'confirmed_expired'", () => {
    const job = makeJob({ staleness_state: "confirmed_expired" });
    render(<JobCard job={job} onAction={mockOnAction} />);
    const badge = screen.getByText("Expired");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveClass("text-red-400");
    expect(screen.queryByText("confirmed_expired")).not.toBeInTheDocument();
  });

  it("F3: shows no badge and doesn't crash for null staleness_state", () => {
    const job = makeJob({ staleness_state: null });
    expect(() => render(<JobCard job={job} onAction={mockOnAction} />)).not.toThrow();
    expect(screen.queryByText(/possibly stale/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/expired/i)).not.toBeInTheDocument();
  });

  it("F3: shows no badge for an unrecognised staleness value (fails safe)", () => {
    const job = makeJob({ staleness_state: "some_future_value" });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.queryByText("some_future_value")).not.toBeInTheDocument();
    expect(screen.queryByText(/expired/i)).not.toBeInTheDocument();
  });

  // ── F4 — score bands: TWO scales need TWO band sets ─────────────────────────
  // Keyword match_score (unjudged) uses green>=45 / amber>=20, derived from the
  // live distribution (median 10, p90 42, max 80). Judged llm_fit_score keeps
  // the old 70/40 bands so it stays matched to the AI-verdict pill's colours.

  it("F4: unjudged match_score of 45 (keyword green boundary) gets score-high", () => {
    const job = makeJob({ match_score: 45, llm_fit_score: null, llm_verdict: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("45")).toHaveClass("score-high");
  });

  it("F4: unjudged match_score of 44 (just under keyword green) gets score-mid", () => {
    const job = makeJob({ match_score: 44, llm_fit_score: null, llm_verdict: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("44")).toHaveClass("score-mid");
  });

  it("F4: unjudged match_score of 20 (keyword amber boundary) gets score-mid", () => {
    const job = makeJob({ match_score: 20, llm_fit_score: null, llm_verdict: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("20")).toHaveClass("score-mid");
  });

  it("F4: unjudged match_score of 19 (just under keyword amber) gets score-low", () => {
    const job = makeJob({ match_score: 19, llm_fit_score: null, llm_verdict: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("19")).toHaveClass("score-low");
  });

  it("F4: unjudged match_score of 70 (old threshold) is NOT high under the keyword scale", () => {
    // 70 clears the OLD single threshold but is still >= the new keyword green (45),
    // so it should still be score-high — this pins that a keyword score of 70
    // does not fall through to score-mid/score-low under the new bands.
    const job = makeJob({ match_score: 70, llm_fit_score: null, llm_verdict: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("70")).toHaveClass("score-high");
  });

  it("F4: judged llm_fit_score of 70 (judge green boundary) gets score-high", () => {
    const job = makeJob({
      match_score: 30,
      llm_fit_score: 70,
      llm_verdict: "strong_fit",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("70")).toHaveClass("score-high");
  });

  it("F4: judged llm_fit_score of 69 (just under judge green) gets score-mid", () => {
    const job = makeJob({
      match_score: 30,
      llm_fit_score: 69,
      llm_verdict: "moderate_fit",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("69")).toHaveClass("score-mid");
  });

  it("F4: judged llm_fit_score of 40 (judge amber boundary) gets score-mid", () => {
    const job = makeJob({
      match_score: 30,
      llm_fit_score: 40,
      llm_verdict: "moderate_fit",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("40")).toHaveClass("score-mid");
  });

  it("F4: judged llm_fit_score of 39 (just under judge amber) gets score-low", () => {
    const job = makeJob({
      match_score: 30,
      llm_fit_score: 39,
      llm_verdict: "weak_fit",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("39")).toHaveClass("score-low");
  });

  it("F4: a judged score of 45 (keyword green, but under judge amber) stays score-low — scale must not leak", () => {
    // 45 clears the KEYWORD green boundary but is judged, so the judge bands
    // (70/40) must apply, not the keyword bands — proves the two scales don't
    // cross-contaminate.
    const job = makeJob({
      match_score: 10,
      llm_fit_score: 45,
      llm_verdict: "moderate_fit",
    });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("45")).toHaveClass("score-mid");
  });
});
