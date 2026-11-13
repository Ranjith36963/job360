import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SentOnItsOwn } from "./SentOnItsOwn";

const getSentOnItsOwn = vi.fn();
const reportWrongFact = vi.fn();
vi.mock("@/lib/api", () => ({
  getSentOnItsOwn: (...a: unknown[]) => getSentOnItsOwn(...a),
  reportWrongFact: (...a: unknown[]) => reportWrongFact(...a),
}));
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn() } }));

const evil = "<img src=x onerror=alert(1)>";
const item = {
  id: 4, application_id: 7, job_company: "Acme", job_title: "Data Engineer", kind: "essay",
  question: "Why Acme?", answer: evil, sources: [{ label: "Your CV", text: "Built Spark pipelines" }],
  sent_at: "2026-11-02T10:00:00Z", reported_wrong: false,
};

beforeEach(() => {
  getSentOnItsOwn.mockReset();
  reportWrongFact.mockReset();
});

describe("Sent on its own", () => {
  it("shows nothing while the server has no items, and nothing on a failed read", async () => {
    getSentOnItsOwn.mockResolvedValue({ items: [] });
    const { container, unmount } = render(<SentOnItsOwn />);
    await waitFor(() => expect(getSentOnItsOwn).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    getSentOnItsOwn.mockRejectedValue(new Error("down"));
    const failed = render(<SentOnItsOwn />);
    await waitFor(() => expect(getSentOnItsOwn).toHaveBeenCalledTimes(2));
    expect(failed.container).toBeEmptyDOMElement();
  });

  it("lists each item as text with its sources, and Wrong fact reports it", async () => {
    getSentOnItsOwn.mockResolvedValue({ items: [item] });
    reportWrongFact.mockResolvedValue(undefined);
    render(<SentOnItsOwn />);
    expect(await screen.findByTestId("sent-4")).toHaveTextContent(evil);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("Sources (1)")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("sent-wrong-4"));
    expect(await screen.findByTestId("sent-reported-4")).toHaveTextContent("You reported a wrong fact");
    expect(reportWrongFact).toHaveBeenCalledWith(4);
  });

  it("shows the approval tag the server sent, and no tag when it sent none", async () => {
    const tagged = { ...item, id: 5, approval: { by: "ai", what: "outreach", at: item.sent_at, ref: 5, ref_kind: "outreach", reviewer: null } };
    getSentOnItsOwn.mockResolvedValue({ items: [tagged, { ...item, id: 6 }] });
    render(<SentOnItsOwn />);
    const li = await screen.findByTestId("sent-5");
    expect(li.querySelector("[data-testid=approval-tag]")?.textContent).toBe("AI reviewed · Message");
    expect(screen.getByTestId("sent-6").querySelector("[data-testid=approval-tag]")).toBeNull();
  });
});
