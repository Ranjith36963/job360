import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { StatusMenu } from "./StatusMenu";

const recordApplicationEvent = vi.fn();

vi.mock("@/lib/api", () => ({
  recordApplicationEvent: (...args: unknown[]) => recordApplicationEvent(...args),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function choose(value: string) {
  fireEvent.change(screen.getByTestId("status-menu"), { target: { value } });
}

function type(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}

describe("StatusMenu", () => {
  const onRecorded = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    recordApplicationEvent.mockReset();
    recordApplicationEvent.mockResolvedValue({});
    onRecorded.mockClear();
  });

  it("a plain status still asks for a confirm and sends only the event type", async () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("replied");
    expect(screen.queryByTestId("status-form-submit")).toBeNull();
    fireEvent.click(screen.getByTestId("status-confirm"));
    await waitFor(() => expect(recordApplicationEvent).toHaveBeenCalledWith(7, { event_type: "replied" }));
    expect(onRecorded).toHaveBeenCalled();
  });

  it("interview_scheduled sends the local time as ISO with its offset, plus the details", async () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("interview_scheduled");
    expect(screen.getByTestId("status-form-interview")).toBeTruthy();
    expect(screen.getByTestId("status-form-duration")).toHaveProperty("value", "60");
    // The date and time is the one required field.
    expect(screen.getByTestId("status-form-submit")).toBeDisabled();

    type("status-form-when", "2026-11-03T14:30");
    type("status-form-round", "technical");
    type("status-form-link", "https://meet.example.com/abc");
    type("status-form-duration", "45");
    type("status-form-location", "Leeds office");
    fireEvent.click(screen.getByTestId("status-form-submit"));

    await waitFor(() => expect(recordApplicationEvent).toHaveBeenCalledTimes(1));
    expect(recordApplicationEvent).toHaveBeenCalledWith(7, {
      event_type: "interview_scheduled",
      scheduled_at: new Date("2026-11-03T14:30").toISOString(),
      payload: {
        round: "technical",
        link: "https://meet.example.com/abc",
        duration_minutes: 45,
        location: "Leeds office",
      },
    });
    expect(onRecorded).toHaveBeenCalled();
  });

  it("interview_requested needs no time and omits empty fields", async () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("interview_requested");
    fireEvent.click(screen.getByTestId("status-form-submit"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(7, {
        event_type: "interview_requested",
        payload: { duration_minutes: 60 },
      })
    );
  });

  it("blocks a link that is not http(s) and a length outside 5 to 480", () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("interview_requested");
    type("status-form-link", "ftp://x.example.com");
    expect(screen.getByTestId("status-form-submit")).toBeDisabled();
    expect(screen.getByTestId("status-form-problem").textContent).toContain("http");
    type("status-form-link", "");
    type("status-form-duration", "481");
    expect(screen.getByTestId("status-form-submit")).toBeDisabled();
    type("status-form-duration", "480");
    expect(screen.getByTestId("status-form-submit")).not.toBeDisabled();
  });

  it("offer sends the three offer figures together, with GBP and per year as defaults", async () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("offer");
    expect(screen.getByTestId("status-form-offer")).toBeTruthy();
    type("status-form-amount", "85000");
    fireEvent.click(screen.getByTestId("status-form-submit"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(7, {
        event_type: "offer",
        payload: { offer_amount: 85000, offer_currency: "GBP", offer_period: "year" },
      })
    );
  });

  it("offer with no amount sends no payload and a lower-case currency is made upper-case", async () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("offer");
    type("status-form-currency", "eur");
    expect(screen.getByTestId("status-form-currency")).toHaveProperty("value", "EUR");
    fireEvent.click(screen.getByTestId("status-form-submit"));
    await waitFor(() => expect(recordApplicationEvent).toHaveBeenCalledWith(7, { event_type: "offer" }));
  });

  it("rejected turns comma-separated reasons into a list and sends the stage", async () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("rejected");
    expect(screen.getByTestId("status-form-rejected")).toBeTruthy();
    type("status-form-stage", "After the first interview");
    type("status-form-reasons", " too junior, ,no visa ");
    fireEvent.click(screen.getByTestId("status-form-submit"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(7, {
        event_type: "rejected",
        payload: { stage: "After the first interview", reasons: ["too junior", "no visa"] },
      })
    );
  });

  it("Cancel closes the form without recording anything", () => {
    render(<StatusMenu applicationId={7} onRecorded={onRecorded} />);
    choose("rejected");
    fireEvent.click(screen.getByTestId("status-cancel"));
    expect(screen.queryByTestId("status-form-rejected")).toBeNull();
    expect(recordApplicationEvent).not.toHaveBeenCalled();
  });
});
