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
});
