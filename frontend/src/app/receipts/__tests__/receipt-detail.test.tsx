import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import ReceiptDetailPage from "../[id]/page";

const getReceipt = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "12" }),
}));
vi.mock("@/lib/api", () => ({
  getReceipt: (...args: unknown[]) => getReceipt(...args),
}));

const BASE = {
  id: 12,
  job_id: 3,
  job_title: "Platform Engineer",
  job_company: "Globex",
  job_location: "London",
  job_source: "brought",
  job_apply_url: "https://example.com/apply",
  job_description: "Build the platform.",
  sent_at: "2026-09-20T10:30:00Z",
  channel: "email",
  note: "sent on a Friday",
  profile_version: 4,
  cv_text: "My CV text",
  cv_origin: "artifact",
  cover_letter_text: "Dear team",
  cover_letter_origin: "polished",
};

beforeEach(() => {
  getReceipt.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("/receipts/[id]", () => {
  it("shows what was recorded at apply time: answers, fields, confirmation, versions, who, the application", async () => {
    getReceipt.mockResolvedValue({
      ...BASE,
      application_id: 52,
      answers: [{ question: "Why us?", answer: "I built one." }],
      fields_filled: { salary_expectation: 45000, notice: "1 month" },
      confirmation: "MIS-48213",
      cv_version_no: 3,
      cover_letter_version_no: 1,
      recorded_by: "agent:Claude",
    });
    render(<ReceiptDetailPage />);
    await screen.findByTestId("receipt-title");

    const answers = screen.getByTestId("receipt-answers");
    expect(within(answers).getByRole("heading", { level: 2, name: "Answers given" })).toBeInTheDocument();
    expect(within(answers).getByText("Why us?")).toBeInTheDocument();
    expect(within(answers).getByText("I built one.")).toBeInTheDocument();

    const fields = screen.getByTestId("receipt-fields");
    expect(within(fields).getByText("salary_expectation")).toBeInTheDocument();
    expect(within(fields).getByText("45000")).toBeInTheDocument();
    expect(within(fields).getByText("1 month")).toBeInTheDocument();

    expect(screen.getByText("MIS-48213")).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "APP-052" })).toHaveAttribute("href", "/applications/52");
  });

  it("an older receipt with none of that stays silent: no empty sections, no placeholder rows", async () => {
    getReceipt.mockResolvedValue({
      ...BASE,
      application_id: null,
      answers: [],
      fields_filled: {},
      confirmation: null,
      cv_version_no: null,
      cover_letter_version_no: null,
      recorded_by: null,
    });
    render(<ReceiptDetailPage />);
    await screen.findByTestId("receipt-title");

    expect(screen.queryByTestId("receipt-answers")).toBeNull();
    expect(screen.queryByTestId("receipt-fields")).toBeNull();
    expect(screen.queryByText("Confirmation")).toBeNull();
    expect(screen.queryByText("Recorded by")).toBeNull();
    expect(screen.queryByText("Application")).toBeNull();
    expect(screen.queryByText("Cover letter")).toBeNull();
  });

  it("renders the sheet: masthead id, title, facts and the three sections", async () => {
    getReceipt.mockResolvedValue(BASE);
    render(<ReceiptDetailPage />);

    const title = await screen.findByTestId("receipt-title");
    expect(title).toHaveTextContent("Platform Engineer");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("Globex · London")).toBeInTheDocument();
    expect(screen.getByTestId("logo")).toBeInTheDocument();
    expect(screen.getByText("RECEIPT R-0012")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("v4")).toBeInTheDocument();
    expect(screen.getByText(/sent on a Friday/)).toBeInTheDocument();

    expect(
      within(screen.getByTestId("receipt-cv")).getByRole("heading", { level: 2, name: "CV you sent" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("receipt-cv")).toHaveTextContent("My CV text");
    expect(screen.getByTestId("receipt-cover-letter")).toHaveTextContent("Cover letter you sent");
    expect(screen.getByTestId("receipt-cover-letter")).toHaveTextContent("Dear team");
    expect(screen.getByTestId("receipt-ad")).toHaveTextContent("The ad, as it read that day");
    expect(screen.getByTestId("receipt-ad")).toHaveTextContent("Build the platform.");
    expect(screen.getByRole("link", { name: "All receipts" })).toHaveAttribute("href", "/receipts");
  });

  it("maps origins: artifact, the two legacy values, and hides unknown codes", async () => {
    getReceipt.mockResolvedValue(BASE);
    const { unmount } = render(<ReceiptDetailPage />);
    await screen.findByTestId("receipt-title");
    expect(screen.getByTestId("receipt-cv")).toHaveTextContent("saved by your assistant");
    expect(screen.getByTestId("receipt-cover-letter")).toHaveTextContent("your edited version");
    unmount();

    getReceipt.mockResolvedValue({ ...BASE, cv_origin: "ai_draft", cover_letter_origin: "mystery_code" });
    render(<ReceiptDetailPage />);
    await screen.findByTestId("receipt-title");
    expect(screen.getByTestId("receipt-cv")).toHaveTextContent("the AI draft, unedited");
    expect(screen.getByTestId("receipt-cover-letter")).not.toHaveTextContent("mystery_code");
  });

  it("shows the empty copy when nothing was tailored", async () => {
    getReceipt.mockResolvedValue({ ...BASE, cv_text: null, cover_letter_text: null, cv_origin: null });
    render(<ReceiptDetailPage />);
    await screen.findByTestId("receipt-title");
    const copy = "Nothing was tailored in Job360 for this one — you sent your own file.";
    expect(within(screen.getByTestId("receipt-cv")).getByText(copy)).toBeInTheDocument();
    expect(within(screen.getByTestId("receipt-cover-letter")).getByText(copy)).toBeInTheDocument();
  });

  it("Download PDF calls window.print", async () => {
    getReceipt.mockResolvedValue(BASE);
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    render(<ReceiptDetailPage />);
    await screen.findByTestId("receipt-title");
    fireEvent.click(screen.getByRole("button", { name: /Download PDF/ }));
    expect(print).toHaveBeenCalledTimes(1);
  });

  it("shows Receipt not found when the load fails", async () => {
    getReceipt.mockRejectedValue(new Error("404"));
    render(<ReceiptDetailPage />);
    expect(await screen.findByText("Receipt not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All receipts/ })).toBeInTheDocument();
  });
});
