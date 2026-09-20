import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AlignmentPanel } from "./AlignmentPanel";
import type { Alignment } from "@/lib/api";

const getAlignment = vi.fn();
vi.mock("@/lib/api", () => ({
  getAlignment: (...args: unknown[]) => getAlignment(...args),
}));

function payload(overrides: Partial<Alignment> = {}): Alignment {
  return {
    fit: {
      score: 68,
      verdict: "Strong match on NLP",
      gaps: ["leading university", "5 years management"],
      reasoning: null,
      axes: [],
      recorded_by: "agent:Claude",
      recorded_at: "2026-09-20T09:04:11Z",
    },
    skills_in_ad: ["Python", "RAG"],
    skills_not_in_ad: ["LangGraph", "Kubernetes"],
    skills_total: 4,
    ad_chars: 900,
    ...overrides,
  };
}

describe("AlignmentPanel", () => {
  beforeEach(() => getAlignment.mockReset());

  it("renders the score bar, verdict, gap pills, and skill columns", async () => {
    getAlignment.mockResolvedValue(payload());
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() => expect(screen.getByText("Strong match on NLP")).toBeInTheDocument());

    const track = screen.getByTestId("fit-score-bar");
    const fill = track.firstElementChild as HTMLElement;
    expect(fill).toHaveStyle({ width: "68%" });
    expect(screen.getByText("68/100")).toBeInTheDocument();

    expect(screen.getAllByTestId("fit-gap")).toHaveLength(2);

    expect(
      screen.getByText("2 of 4 of your skills appear in this ad")
    ).toBeInTheDocument();

    const inAd = screen.getByTestId("skills-in-ad");
    expect(inAd.querySelectorAll("li")).toHaveLength(2);
    expect(inAd).toHaveTextContent("Python");
    expect(inAd).toHaveTextContent("RAG");

    const notInAd = screen.getByTestId("skills-not-in-ad");
    expect(notInAd.querySelectorAll("li")).toHaveLength(2);
    expect(notInAd).toHaveTextContent("LangGraph");
    expect(notInAd).toHaveTextContent("Kubernetes");
  });

  it("shows the no-fit-judgement line and still shows the skill columns when fit is null", async () => {
    getAlignment.mockResolvedValue(payload({ fit: null }));
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() =>
      expect(
        screen.getByText("No fit judgement yet — your agent saves one with save_fit.")
      ).toBeInTheDocument()
    );

    expect(screen.getByTestId("skills-in-ad")).toBeInTheDocument();
    expect(screen.getByTestId("skills-not-in-ad")).toBeInTheDocument();
    expect(screen.queryByTestId("fit-score-bar")).toBeNull();
  });

  it("shows the error line when the fetch rejects", async () => {
    getAlignment.mockRejectedValueOnce(new Error("boom"));
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() =>
      expect(screen.getByText("Could not load the fit picture.")).toBeInTheDocument()
    );
  });

  it("folds 'Not in the ad' past 12 pills, and 'Show all' expands to the full list", async () => {
    const thirteenSkills = Array.from({ length: 13 }, (_, i) => `Skill${i + 1}`);
    getAlignment.mockResolvedValue(
      payload({
        skills_in_ad: [],
        skills_not_in_ad: thirteenSkills,
        skills_total: 13,
      })
    );
    render(<AlignmentPanel applicationId={8383} />);

    const notInAd = await screen.findByTestId("skills-not-in-ad");
    await waitFor(() => expect(notInAd.querySelectorAll("li")).toHaveLength(12));

    const showAll = screen.getByTestId("skills-show-all");
    expect(showAll).toHaveTextContent("Show all 13");

    fireEvent.click(showAll);

    expect(notInAd.querySelectorAll("li")).toHaveLength(13);
    expect(showAll).toHaveTextContent("Show fewer");
  });
});
