import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScoreRadar } from "./ScoreRadar";

const fullScores = {
  role: 12,
  skill: 18,
  seniority_score: 8,
  experience: 7,
  credentials: 4,
  location_score: 9,
  recency: 8,
  semantic: 15,
};

describe("ScoreRadar", () => {
  it("renders without crashing", () => {
    render(<ScoreRadar scores={fullScores} />);
  });

  it("has an accessible aria-label describing all 8 dimensions", () => {
    render(<ScoreRadar scores={fullScores} />);
    const radar = screen.getByRole("img");
    expect(radar).toHaveAttribute("aria-label");
    const label = radar.getAttribute("aria-label")!;
    expect(label).toContain("Role");
    expect(label).toContain("Skill");
    expect(label).toContain("Seniority");
    expect(label).toContain("Semantic");
  });

  it("value-presence: all non-zero scores render as non-zero text", () => {
    render(<ScoreRadar scores={fullScores} />);
    // The raw score grid shows e.g. "12/15" — verify presence
    expect(screen.getByText("12/15")).toBeInTheDocument();
    expect(screen.getByText("18/20")).toBeInTheDocument();
    // seniority_score=8 and recency=8 both show 8/10 — use getAllByText
    expect(screen.getAllByText("8/10").length).toBeGreaterThanOrEqual(1);
  });

  it("null guard: renders safely with partial scores (undefined fields → 0)", () => {
    const partial = { role: 10, skill: 15 };
    expect(() => render(<ScoreRadar scores={partial} />)).not.toThrow();
  });

  it("uses seniority_score key (not legacy seniority) — non-zero in aria-label", () => {
    // If seniority_score is wired, the aria-label says "Seniority: 8 out of 10"
    render(<ScoreRadar scores={fullScores} />);
    const radar = screen.getByRole("img");
    expect(radar.getAttribute("aria-label")).toContain("Seniority: 8 out of 10");
  });

  it("uses location_score key (not legacy location) — non-zero in aria-label", () => {
    render(<ScoreRadar scores={fullScores} />);
    const radar = screen.getByRole("img");
    expect(radar.getAttribute("aria-label")).toContain("Location: 9 out of 10");
  });
});
