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
    // The deadline trigger exposes "Apply by <formatted date>" as its accessible
    // name (aria-label). Asserting on that is robust to the Base UI tooltip's
    // render-prop children not rendering in jsdom (they render fine in a browser).
    expect(screen.getByLabelText(/Apply by 18 Jun 2026/i)).toBeInTheDocument();
  });

  // D-5: renders "No deadline listed" when deadline is null
  it("D-5: renders 'No deadline listed' when deadline is null", () => {
    const job = makeJob({ deadline: null });
    render(<JobCard job={job} onAction={mockOnAction} />);
    expect(screen.getByText("No deadline listed")).toBeInTheDocument();
  });
});
