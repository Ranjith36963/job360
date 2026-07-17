import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ScoreRadar } from "./ScoreRadar";

/**
 * ScoreRadar — the 8 dimensions the ENGINE actually computes.
 *
 * History these tests now guard against: the radar shipped with 8 INVENTED
 * axes whose maxes summed to a tidy 100 (role 15 / skill 20 / experience 10 /
 * credentials 5 / semantic 20 …), a mockup that predated the engine. The old
 * tests pinned those invented maxes ("12/15", "18/20"), so they cemented the
 * fiction rather than catching it. Real weights (skill_matcher.py +
 * settings.py): Title 40 / Skill 40 / Location 10 / Recency 10 / Seniority 8 /
 * Salary 10 / Visa 6 / Workplace 6.
 */

// A fully-enriched job: every dimension measured.
const fullScores = {
  role: 32,
  skill: 30,
  location_score: 9,
  recency: 8,
  seniority_score: 8,
  salary_score: 7,
  visa_score: 6,
  workplace_score: 3,
};

describe("ScoreRadar", () => {
  it("renders without crashing", () => {
    render(<ScoreRadar scores={fullScores} dimsActive />);
  });

  it("has an accessible aria-label describing all 8 real dimensions", () => {
    render(<ScoreRadar scores={fullScores} dimsActive />);
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    for (const dim of [
      "Role",
      "Skill",
      "Location",
      "Recency",
      "Seniority",
      "Salary",
      "Visa",
      "Workplace",
    ]) {
      expect(label).toContain(dim);
    }
  });

  it("reports each dimension against the ENGINE's real max, not an invented one", () => {
    render(<ScoreRadar scores={fullScores} dimsActive />);
    // role/skill are out of 40 (TITLE_WEIGHT/SKILL_WEIGHT) — NOT 15/20.
    expect(screen.getByText("32/40")).toBeInTheDocument();
    expect(screen.getByText("30/40")).toBeInTheDocument();
    // seniority is out of 8 (SENIORITY_WEIGHT) — NOT 10.
    expect(screen.getByText("8/8")).toBeInTheDocument();
    // the previously-discarded dims are now visible.
    expect(screen.getByText("7/10")).toBeInTheDocument(); // salary
    expect(screen.getByText("6/6")).toBeInTheDocument(); // visa
    expect(screen.getByText("3/6")).toBeInTheDocument(); // workplace
  });

  it("a full title match reads 40 out of 40 (previously computed 267%)", () => {
    render(<ScoreRadar scores={{ ...fullScores, role: 40 }} dimsActive />);
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("Role: 40 out of 40");
  });

  it("survives a partial payload", () => {
    expect(() =>
      render(<ScoreRadar scores={{ role: 30, skill: 20 }} dimsActive />)
    ).not.toThrow();
  });

  // --- dormant vs earned zero: the distinction that keeps this honest ------

  it("marks enrichment dims NOT MEASURED when enrichment didn't run", () => {
    // rule #21: an always-0 axis is a lie. Without enrichment the four
    // enrichment dims were never measured — say so, don't plot 0%.
    render(<ScoreRadar scores={{ role: 32, skill: 30, location_score: 9, recency: 8 }} dimsActive={false} />);
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("Seniority: not measured");
    expect(label).toContain("Salary: not measured");
    expect(label).toContain("Visa: not measured");
    expect(label).toContain("Workplace: not measured");
    // ...while the legacy four still report real values.
    expect(label).toContain("Role: 32 out of 40");
    expect(screen.getByText(/aren't measured for this job yet/i)).toBeInTheDocument();
  });

  it("an EARNED zero is reported as a real score, not as 'not measured'", () => {
    // visa_score=0 when the user needs sponsorship and the job offers none —
    // that IS a judgement and must not be hidden behind "not measured".
    render(<ScoreRadar scores={{ ...fullScores, visa_score: 0 }} dimsActive />);
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("Visa: 0 out of 6");
    expect(label).not.toContain("Visa: not measured");
  });

  it("dormant axes are excluded from the average, not averaged as zero", () => {
    // Otherwise "enrichment didn't run" would drag the headline % down as if
    // the job scored badly.
    render(<ScoreRadar scores={{ role: 40, skill: 40, location_score: 10, recency: 10 }} dimsActive={false} />);
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("Average 100%");
  });

  // --- geometry guards ----------------------------------------------------

  it("clamps a NEGATIVE dim (seniority penalty) without inverting the polygon", () => {
    // seniority_score is legitimately negative down to -SENIORITY_WEIGHT.
    render(<ScoreRadar scores={{ ...fullScores, seniority_score: -8 }} dimsActive />);
    const radar = screen.getByRole("img");
    // raw reported verbatim — we never lie about the number...
    expect(radar.getAttribute("aria-label")).toContain("Seniority: -8 out of 8");
    expect(radar).toBeInTheDocument(); // ...and the chart still renders.
  });

  it("average percentage stays within 0..100 even with wild inputs", () => {
    render(
      <ScoreRadar
        scores={{ role: 999, skill: 40, seniority_score: -8, salary_score: 500 }}
        dimsActive
      />
    );
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    const avg = Number(/Average (-?\d+)%/.exec(label)?.[1]);
    expect(avg).toBeGreaterThanOrEqual(0);
    expect(avg).toBeLessThanOrEqual(100);
  });
});
