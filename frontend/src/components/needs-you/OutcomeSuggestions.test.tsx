import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OutcomeSuggestions } from "./OutcomeSuggestions";

const listOutcomeSuggestions = vi.fn();
const recordApplicationEvent = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock("@/lib/api", () => ({
  listOutcomeSuggestions: (...a: unknown[]) => listOutcomeSuggestions(...a),
  recordApplicationEvent: (...a: unknown[]) => recordApplicationEvent(...a),
}));
vi.mock("sonner", () => ({
  toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) },
}));

const suggestion = {
  event_id: 41,
  application_id: 7,
  job_title: "Data Engineer",
  job_company: "Northwind Ltd",
  status: "applied",
  event_type: "rejected",
  quote: "We have decided not to proceed.",
  why: "Might be about the other Northwind role",
  occurred_at: "2026-10-02T08:00:00+00:00",
  recorded_at: "2026-10-02T08:05:00+00:00",
  recorded_by: "agent",
  email: {
    kind: "email",
    message_id: "<s-1@northwind.example>",
    thread_id: null,
    sender: "hr@northwind.example",
    subject: "Update",
    received_at: "2026-10-02T08:00:00+00:00",
    quote: null,
  },
};

describe("OutcomeSuggestions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    recordApplicationEvent.mockResolvedValue({});
  });

  it("shows the suggestion and Accept records exactly that event with the quote", async () => {
    listOutcomeSuggestions.mockResolvedValueOnce({ suggestions: [suggestion], truncated: false });
    listOutcomeSuggestions.mockResolvedValue({ suggestions: [], truncated: false });
    render(<OutcomeSuggestions />);
    expect(await screen.findByText("Suggested outcomes")).toBeInTheDocument();
    expect(screen.getByTestId("outcome-suggestion-type")).toHaveTextContent("Suggested: Rejected");
    expect(screen.getByTestId("outcome-suggestion-quote")).toHaveTextContent("We have decided not to proceed.");
    expect(screen.getByTestId("outcome-suggestion-why")).toHaveTextContent("Might be about the other Northwind role");
    fireEvent.click(screen.getByTestId("outcome-suggestion-accept"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          event_type: "rejected",
          source: expect.objectContaining({
            message_id: "<s-1@northwind.example>",
            quote: "We have decided not to proceed.",
          }),
        }),
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("outcome-suggestions")).toBeNull());
    expect(toastSuccess).toHaveBeenCalledWith("Recorded.");
  });

  it("Ignore records outcome_dismissed for that suggestion", async () => {
    listOutcomeSuggestions.mockResolvedValueOnce({ suggestions: [suggestion], truncated: false });
    listOutcomeSuggestions.mockResolvedValue({ suggestions: [], truncated: false });
    render(<OutcomeSuggestions />);
    fireEvent.click(await screen.findByTestId("outcome-suggestion-ignore"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(7, {
        event_type: "outcome_dismissed",
        payload: { suggestion_event_id: 41 },
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith("Ignored.");
  });

  it("renders nothing for an empty list or a failed read", async () => {
    listOutcomeSuggestions.mockResolvedValue({ suggestions: [], truncated: false });
    const { container, rerender } = render(<OutcomeSuggestions />);
    await waitFor(() => expect(listOutcomeSuggestions).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    listOutcomeSuggestions.mockRejectedValue(new Error("boom"));
    rerender(<OutcomeSuggestions />);
    expect(container).toBeEmptyDOMElement();
  });
});
