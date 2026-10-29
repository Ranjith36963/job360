import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Timeline } from "./Timeline";
import type { ApplicationEvent } from "@/lib/api";

const setHidden = vi.fn().mockResolvedValue({ event_id: 9 });
vi.mock("@/lib/api", () => ({ setHidden: (...args: unknown[]) => setHidden(...args) }));
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn() } }));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeEvent(overrides: Partial<ApplicationEvent> = {}): ApplicationEvent {
  return {
    id: 1,
    event_type: "replied",
    detail: "",
    payload: {},
    occurred_at: "2026-09-07T10:00:00+00:00",
    recorded_at: "2026-09-07T10:00:00+00:00",
    recorded_by: "agent",
    corrects_event_id: null,
    superseded: false,
    source: null,
    scheduled_at: null,
    process: false,
    ...overrides,
  };
}

describe("Timeline", () => {
  it("shows the verbatim email quote under the source line", () => {
    render(
      <Timeline
        events={[
          makeEvent({
            source: {
              kind: "email", message_id: "<a>", thread_id: null, sender: "hr@x.example",
              subject: "Update", received_at: null, quote: "We will not be moving forward.",
            },
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("event-source-quote")).toHaveTextContent("We will not be moving forward.");
  });

  it("shows who recorded each event in plain words", () => {
    render(
      <Timeline
        events={[
          makeEvent({ id: 1, recorded_by: "web" }),
          makeEvent({ id: 2, recorded_by: "agent:Claude" }),
          makeEvent({ id: 3, recorded_by: "token:My laptop" }),
          makeEvent({ id: 4, recorded_by: "system" }),
        ]}
      />,
    );
    const marks = screen.getAllByTestId("event-actor").map((m) => m.textContent);
    expect(marks).toEqual([
      "recorded by you",
      "recorded by your assistant (via Claude)",
      "recorded by your assistant (via My laptop)",
      "recorded by Job360",
    ]);
  });

  it("renders the sender and subject for an event with a source, and no anchor", () => {
    render(
      <Timeline
        events={[
          makeEvent({
            source: {
              kind: "email",
              message_id: "abc",
              sender: "recruiter@acme.example",
              subject: "Your application",
              received_at: "2026-09-07T09:00:00+00:00",
            },
          }),
        ]}
      />
    );

    expect(screen.getByText(/recruiter@acme\.example/)).toBeInTheDocument();
    expect(screen.getByText(/your application/i)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows 'Scheduled for' when the event carries scheduled_at", () => {
    render(
      <Timeline
        events={[
          makeEvent({
            event_type: "interview_scheduled",
            scheduled_at: "2026-09-15T09:00:00+00:00",
          }),
        ]}
      />
    );

    expect(screen.getByText(/scheduled for/i)).toBeInTheDocument();
  });

  it("shows an offer's stored figures in plain words", () => {
    render(
      <Timeline
        events={[
          makeEvent({
            event_type: "offer",
            payload: { offer_amount: 85000, offer_currency: "GBP", offer_period: "year" },
          }),
        ]}
      />
    );

    expect(screen.getByTestId("event-offer").textContent).toBe("Offer: 85,000 GBP per year");
  });

  it("shows an interview's round, length and place, and nothing for a bare offer", () => {
    render(
      <Timeline
        events={[
          makeEvent({
            id: 1,
            event_type: "interview_scheduled",
            payload: { round: "system-design", duration_minutes: 45, location: "Leeds office" },
          }),
          makeEvent({ id: 2, event_type: "offer", payload: {} }),
        ]}
      />
    );

    expect(screen.getByTestId("event-interview").textContent).toBe("System design · 45 min · Leeds office");
    expect(screen.queryByTestId("event-offer")).toBeNull();
  });

  it("folds process events behind a toggle and never strikes them through", () => {
    render(
      <Timeline
        events={[
          makeEvent({ id: 1, event_type: "note", detail: "old note", superseded: true, process: true }),
          makeEvent({ id: 2, event_type: "process_log", detail: "checked the form", process: true }),
          makeEvent({ id: 3, event_type: "replied", detail: "visible" }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("timeline-event")).toHaveLength(1);
    const toggle = screen.getByTestId("work-log-toggle");
    expect(toggle).toHaveTextContent("Show the assistant's work log (2)");
    fireEvent.click(toggle);
    expect(screen.getAllByTestId("timeline-event")).toHaveLength(3);
    expect(screen.getByText("checked the form")).toBeInTheDocument();
    expect(screen.getByText("Assistant's work log")).toBeInTheDocument();
  });

  it("shows no toggle when there are no process events", () => {
    render(<Timeline events={[makeEvent()]} />);
    expect(screen.queryByTestId("work-log-toggle")).toBeNull();
  });
});

describe("Timeline — Hide from timeline (S11a)", () => {
  it("offers Hide on a plain note only, and Show in timeline on a hidden one", async () => {
    const onChanged = vi.fn();
    render(
      <Timeline
        applicationId={108}
        onChanged={onChanged}
        events={[
          makeEvent({ id: 1, event_type: "applied" }),
          makeEvent({ id: 2, event_type: "note", detail: "Doctor self-review." }),
          makeEvent({ id: 3, event_type: "note", payload: { by: "x" } }),
        ]}
      />,
    );
    const buttons = screen.getAllByTestId("event-hide");
    expect(buttons.map((b) => b.textContent)).toEqual(["Hide from timeline"]);
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(setHidden).toHaveBeenCalledWith(108, { eventId: 2 }, "hide");
  });

  it("shows a hidden note again from the work log", async () => {
    render(
      <Timeline applicationId={108} events={[makeEvent({ id: 5, event_type: "note", process: true })]} />,
    );
    fireEvent.click(screen.getByTestId("work-log-toggle"));
    fireEvent.click(screen.getByTestId("event-hide"));
    await waitFor(() => expect(setHidden).toHaveBeenCalledWith(108, { eventId: 5 }, "show"));
  });

  it("shows no hide buttons without an application id", () => {
    render(<Timeline events={[makeEvent({ event_type: "note" })]} />);
    expect(screen.queryByTestId("event-hide")).toBeNull();
  });
});
