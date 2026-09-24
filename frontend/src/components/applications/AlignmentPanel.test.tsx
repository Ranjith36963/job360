import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

  it("renders the score bar, verdict, labelled gap pills, and the one-line skills summary", async () => {
    getAlignment.mockResolvedValue(payload());
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() => expect(screen.getByText("Strong match on NLP")).toBeInTheDocument());

    const track = screen.getByTestId("fit-score-bar");
    const fill = track.firstElementChild as HTMLElement;
    expect(fill).toHaveStyle({ width: "68%" });
    expect(screen.getByText("68/100")).toBeInTheDocument();

    expect(
      screen.getByText("What the job asks for that you lack — from your assistant")
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("fit-gap")).toHaveLength(2);

    expect(screen.getByText("This ad mentions 2 of your skills")).toBeInTheDocument();

    const inAd = screen.getByTestId("skills-in-ad");
    expect(inAd.querySelectorAll("li")).toHaveLength(2);
    expect(inAd).toHaveTextContent("Python");
    expect(inAd).toHaveTextContent("RAG");

    expect(screen.queryByTestId("skills-not-in-ad")).toBeNull();
    expect(screen.queryByText(/not in the ad/i)).toBeNull();
  });

  it("shows one line and nothing else when fit is null", async () => {
    getAlignment.mockResolvedValue(payload({ fit: null }));
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() =>
      expect(
        screen.getByText("No fit yet — ask your assistant to judge this job.")
      ).toBeInTheDocument()
    );

    expect(screen.queryByTestId("skills-in-ad")).toBeNull();
    expect(screen.queryByTestId("fit-score-bar")).toBeNull();
  });

  it("shows the error line when the fetch rejects", async () => {
    getAlignment.mockRejectedValueOnce(new Error("boom"));
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() =>
      expect(screen.getByText("Could not load the fit picture.")).toBeInTheDocument()
    );
  });

  it("shows no skills-in-ad list when the ad mentions none", async () => {
    getAlignment.mockResolvedValue(payload({ skills_in_ad: [], skills_total: 4 }));
    render(<AlignmentPanel applicationId={8383} />);

    await waitFor(() =>
      expect(screen.getByText("This ad mentions 0 of your skills")).toBeInTheDocument()
    );
    expect(screen.queryByTestId("skills-in-ad")).toBeNull();
  });
});
