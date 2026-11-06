import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SentOnItsOwn } from "./SentOnItsOwn";

// Kind "cv" (owner 2026-11-06): a CV that went on a strong review, today's check, and Wrong fact turning
// automatic CV sending off.

const getSentOnItsOwn = vi.fn();
const reportWrongFact = vi.fn();
vi.mock("@/lib/api", () => ({
  getSentOnItsOwn: (...a: unknown[]) => getSentOnItsOwn(...a),
  reportWrongFact: (...a: unknown[]) => reportWrongFact(...a),
}));
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn() } }));

const cv = (id: number, app: number) => ({
  id, application_id: app, job_company: `Acme ${app}`, job_title: "Data Engineer", kind: "cv",
  question: "CV sent after a strong review", answer: `CV version 2 (artifact ${app * 10})`,
  sources: [
    { label: "Review", text: "strong (by fresh helper)" },
    { label: "Lines covered", text: "4 of 4 CV lines quoted from your profile" },
  ],
  sent_at: "2026-11-06T10:00:00Z", reported_wrong: false,
});

beforeEach(() => {
  getSentOnItsOwn.mockReset();
  reportWrongFact.mockReset();
});

describe("Sent on its own: CVs", () => {
  it("shows the company, title, review sources and a link to the application", async () => {
    getSentOnItsOwn.mockResolvedValue({ items: [cv(-12, 7)], cv_sample: [] });
    render(<SentOnItsOwn />);
    const row = await screen.findByTestId("sent--12");
    expect(row).toHaveTextContent("Acme 7 · Data Engineer · CV sent after a strong review");
    expect(row).toHaveTextContent("CV version 2 (artifact 70)");
    expect(row).toHaveTextContent("Review: strong (by fresh helper)");
    expect(row).toHaveTextContent("Lines covered: 4 of 4 CV lines quoted from your profile");
    expect(screen.getByTestId("sent-app--12").getAttribute("href")).toBe("/applications/7");
    expect(row).toHaveTextContent("Wrong fact turns automatic CV sending off at once.");
    expect(screen.queryByTestId("sent-sample--12")).toBeNull();
  });

  it("marks only the day's sample items as Today's check", async () => {
    getSentOnItsOwn.mockResolvedValue({ items: [cv(-12, 7), cv(-13, 8)], cv_sample: [-13] });
    render(<SentOnItsOwn />);
    expect(await screen.findByTestId("sent-sample--13")).toHaveTextContent("Today's check");
    expect(screen.queryByTestId("sent-sample--12")).toBeNull();
  });

  it("Wrong fact posts the item and says automatic CV sending is off", async () => {
    getSentOnItsOwn.mockResolvedValue({ items: [cv(-12, 7)], cv_sample: [-12] });
    reportWrongFact.mockResolvedValue(undefined);
    render(<SentOnItsOwn />);
    fireEvent.click(await screen.findByTestId("sent-wrong--12"));
    expect(await screen.findByTestId("sent-reported--12")).toHaveTextContent(
      "You reported a wrong fact. Automatic CV sending is off.",
    );
    expect(screen.getByTestId("sent-cv-off")).toHaveTextContent("Automatic CV sending is now off.");
    expect(reportWrongFact).toHaveBeenCalledWith(-12);
  });

  it("a failed report changes nothing", async () => {
    getSentOnItsOwn.mockResolvedValue({ items: [cv(-12, 7)], cv_sample: [] });
    reportWrongFact.mockRejectedValue(new Error("down"));
    render(<SentOnItsOwn />);
    fireEvent.click(await screen.findByTestId("sent-wrong--12"));
    expect(await screen.findByTestId("sent-wrong--12")).toBeEnabled();
    expect(screen.queryByTestId("sent-cv-off")).toBeNull();
    expect(screen.queryByTestId("sent-reported--12")).toBeNull();
  });
});
