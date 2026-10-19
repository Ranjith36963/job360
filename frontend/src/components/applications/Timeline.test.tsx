import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
    ...overrides,
  };
}

describe("Timeline", () => {
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
});
