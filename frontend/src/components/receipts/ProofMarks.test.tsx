import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProofMark, ProofMarks, proofTime } from "./ProofMarks";
import type { Proof } from "@/lib/types";

const NONE: Proof = {
  has_text: false,
  has_page_text: false,
  has_confirmation: false,
  has_email: false,
  email_seen_at: null,
  screenshots: 0,
  level: "none",
};
// A local wall-clock time, so the expected text holds in any time zone.
const SEEN = new Date(2026, 9, 2, 18, 52).toISOString();
const chips = () => screen.getAllByTestId("proof-chip").map((c) => c.textContent);

describe("ProofMarks (receipt sheet)", () => {
  it("shows a dashed 'No proof yet' when there is nothing", () => {
    render(<ProofMarks proof={NONE} confirmation="" />);
    expect(screen.getByTestId("proof-none")).toHaveTextContent("No proof yet");
    expect(screen.queryAllByTestId("proof-chip")).toHaveLength(0);
  });

  it("shows 'No proof yet' when proof is missing (receipt without an application)", () => {
    render(<ProofMarks proof={null} confirmation={null} />);
    expect(screen.getByTestId("proof-none")).toHaveTextContent("No proof yet");
  });

  it("matches the mockup: confirmation number with its value, then the email with its time", () => {
    render(
      <ProofMarks
        proof={{ ...NONE, has_text: true, has_confirmation: true, has_email: true, email_seen_at: SEEN, level: "email" }}
        confirmation="MIS-48213"
      />,
    );
    expect(chips()).toEqual(["Confirmation number saved · MIS-48213", "Confirmation email seen · 02 Oct 18:52"]);
  });

  it("a confirmation alone never claims a saved thank-you page", () => {
    render(<ProofMarks proof={{ ...NONE, has_text: true, has_confirmation: true, level: "text" }} confirmation="REF-1" />);
    expect(chips()).toEqual(["Confirmation number saved · REF-1"]);
  });

  it("pasted page text shows even when a confirmation exists too", () => {
    render(
      <ProofMarks
        proof={{ ...NONE, has_text: true, has_confirmation: true, has_page_text: true, level: "text" }}
        confirmation="REF-1"
      />,
    );
    expect(chips()).toEqual(["Confirmation number saved · REF-1", "Thank-you page saved"]);
  });

  it("a long confirmation is not repeated in the mark (over 24 chars)", () => {
    render(<ProofMarks proof={{ ...NONE, has_text: true, has_confirmation: true, level: "text" }} confirmation={"X".repeat(25)} />);
    expect(chips()).toEqual(["Confirmation number saved"]);
  });

  it("email with no time, and screenshots with a count", () => {
    render(<ProofMarks proof={{ ...NONE, has_email: true, screenshots: 2, level: "email" }} />);
    expect(chips()).toEqual(["Confirmation email seen", "Screenshot saved (2)"]);
  });
});

describe("ProofMark (receipts list)", () => {
  it("one mark per stored part, without values", () => {
    render(
      <ProofMark
        proof={{ ...NONE, has_text: true, has_confirmation: true, has_email: true, email_seen_at: SEEN, level: "email" }}
      />,
    );
    expect(screen.getAllByTestId("proof-mark").map((m) => m.textContent)).toEqual([
      "Confirmation number saved",
      "Confirmation email seen",
    ]);
  });

  it("reads 'No proof yet' when proof is absent or empty", () => {
    const { unmount } = render(<ProofMark />);
    expect(screen.getByTestId("proof-mark")).toHaveTextContent("No proof yet");
    unmount();
    render(<ProofMark proof={NONE} />);
    expect(screen.getByTestId("proof-mark")).toHaveTextContent("No proof yet");
  });
});

describe("proofTime", () => {
  it("formats by hand and rejects bad input", () => {
    expect(proofTime(SEEN)).toBe("02 Oct 18:52");
    expect(proofTime("not a date")).toBeNull();
    expect(proofTime(null)).toBeNull();
  });
});
