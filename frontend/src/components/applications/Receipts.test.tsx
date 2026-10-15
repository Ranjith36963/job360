import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { Receipts } from "./Receipts";
import type { ApplicationReceiptEntry } from "@/lib/api";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

type ReceiptAnswer = { question: string; answer: string };
type ReceiptFixture = ApplicationReceiptEntry & {
  answers?: ReceiptAnswer[];
  fields_filled?: Record<string, unknown>;
};

function makeReceipt(overrides: Partial<ReceiptFixture> = {}): ReceiptFixture {
  return {
    id: 1,
    channel: "email",
    confirmation: "",
    note: "",
    cv_artifact_id: null,
    cover_letter_artifact_id: null,
    sent_at: "2026-09-07T10:00:00+00:00",
    ...overrides,
  };
}

describe("Receipts", () => {
  it("renders question and answer text for two answers", () => {
    render(
      <Receipts
        receipts={[
          makeReceipt({
            answers: [
              { question: "Why do you want this role?", answer: "I love the mission." },
              { question: "Notice period?", answer: "2 weeks" },
            ],
          }),
        ]}
      />
    );

    expect(screen.getByText(/why do you want this role\?/i)).toBeInTheDocument();
    expect(screen.getByText(/i love the mission\./i)).toBeInTheDocument();
    expect(screen.getByText(/notice period\?/i)).toBeInTheDocument();
    expect(screen.getByText(/2 weeks/i)).toBeInTheDocument();
    expect(screen.getAllByTestId("receipt-answer")).toHaveLength(2);
  });

  it("renders fields_filled rows sorted alphabetically regardless of input order", () => {
    render(
      <Receipts
        receipts={[
          makeReceipt({
            fields_filled: {
              salary: 65000,
              email: "jane@example.com",
              agree_to_terms: true,
            },
          }),
        ]}
      />
    );

    expect(screen.getByText(/65000/)).toBeInTheDocument();

    const rows = screen.getAllByTestId("receipt-field");
    expect(rows).toHaveLength(3);
    const keys = rows.map((row) => within(row).getByText(/:$/).textContent);
    expect(keys).toEqual(["agree_to_terms:", "email:", "salary:"]);
  });

  it("renders no receipt-answers or receipt-fields blocks when both are empty", () => {
    render(<Receipts receipts={[makeReceipt({ answers: [], fields_filled: {} })]} />);

    expect(screen.queryByTestId("receipt-answers")).not.toBeInTheDocument();
    expect(screen.queryByTestId("receipt-fields")).not.toBeInTheDocument();
  });

  it("renders a legacy receipt with neither key without crashing", () => {
    const legacy: ApplicationReceiptEntry = {
      id: 2,
      channel: "portal",
      confirmation: "",
      note: "",
      cv_artifact_id: null,
      cover_letter_artifact_id: null,
      sent_at: "2026-09-07T10:00:00+00:00",
    };

    render(<Receipts receipts={[legacy]} />);

    expect(screen.getByText("portal")).toBeInTheDocument();
    expect(screen.queryByTestId("receipt-answers")).not.toBeInTheDocument();
    expect(screen.queryByTestId("receipt-fields")).not.toBeInTheDocument();
  });

  it("says the cover letter was not sent when the receipt names none", () => {
    render(<Receipts receipts={[makeReceipt({ cv_artifact_id: 5, cover_letter_artifact_id: null })]} />);

    expect(screen.getByTestId("receipt-no-cover-letter")).toHaveTextContent("Cover letter: not sent");
    expect(screen.getByText("CV #5")).toBeInTheDocument();
  });

  it("shows the letter id and no 'not sent' line when a letter was sent", () => {
    render(<Receipts receipts={[makeReceipt({ cover_letter_artifact_id: 9, cover_letter_sent: true })]} />);

    expect(screen.getByText("Cover letter #9")).toBeInTheDocument();
    expect(screen.queryByTestId("receipt-no-cover-letter")).not.toBeInTheDocument();
  });

  it("shows the counts line, the form host and the sent versions", () => {
    render(
      <Receipts
        receipts={[
          makeReceipt({
            cv_artifact_id: 5,
            cover_letter_artifact_id: 9,
            cover_letter_sent: true,
            cv_version_no: 1,
            cover_letter_version_no: 1,
            form_host: "boards.greenhouse.io",
            counts_line: "6 of 6 fields \u00b7 0 skipped \u00b7 1 guessed",
          }),
        ]}
      />
    );

    expect(screen.getByTestId("receipt-counts")).toHaveTextContent("6 of 6 fields \u00b7 0 skipped \u00b7 1 guessed");
    expect(screen.getByTestId("receipt-via")).toHaveTextContent("via boards.greenhouse.io");
    expect(screen.getByText(/CV v1/)).toBeInTheDocument();
    expect(screen.getByText(/Cover letter v1/)).toBeInTheDocument();
  });

  it("hides the counts line and host when the backend sent none", () => {
    render(<Receipts receipts={[makeReceipt({ counts_line: "", form_host: "" })]} />);

    expect(screen.queryByTestId("receipt-counts")).not.toBeInTheDocument();
    expect(screen.queryByTestId("receipt-via")).not.toBeInTheDocument();
  });
});
