import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Timeline } from "./Timeline";
import type { ApplicationEvent } from "@/lib/api";

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
