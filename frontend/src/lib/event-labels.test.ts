import { describe, it, expect } from "vitest";
import { STATUS_LABEL, EVENT_LABEL, eventLabel, whoLabel } from "./event-labels";

// The closed sets from backend/src/core/settings.py — kept in sync by hand
// (there is no shared schema to import across the language boundary); a
// vocabulary edit there should also edit these two lists.
const APPLICATION_STATUS_EVENT_TYPES = [
  "brought",
  "applied",
  "replied",
  "interview_requested",
  "interview_scheduled",
  "interview_done",
  "offer",
  "rejected",
  "withdrawn",
  "ghosted",
];

const APPLICATION_NOTE_EVENT_TYPES = [
  "fit_judged",
  "artifact_saved",
  "contact_added",
  "outreach_sent",
  "note",
  "lesson",
];

describe("STATUS_LABEL", () => {
  it("has a label for every status a status event can produce", () => {
    // `brought` maps to `considering`, not itself.
    const statuses = APPLICATION_STATUS_EVENT_TYPES.map((t) => (t === "brought" ? "considering" : t));
    for (const status of statuses) {
      expect(STATUS_LABEL[status]).toBeTruthy();
    }
  });
});

describe("EVENT_LABEL", () => {
  it("has a label for every note-family event type", () => {
    for (const type of APPLICATION_NOTE_EVENT_TYPES) {
      expect(EVENT_LABEL[type]).toBeTruthy();
    }
  });
});

describe("eventLabel", () => {
  it("labels every status event as 'Status → <label>'", () => {
    expect(eventLabel({ event_type: "brought" })).toBe("Status → Considering");
    expect(eventLabel({ event_type: "applied" })).toBe("Status → Applied");
    expect(eventLabel({ event_type: "replied" })).toBe("Status → Replied");
    expect(eventLabel({ event_type: "interview_requested" })).toBe("Status → Interview requested");
    expect(eventLabel({ event_type: "interview_scheduled" })).toBe("Status → Interview scheduled");
    expect(eventLabel({ event_type: "interview_done" })).toBe("Status → Interview done");
    expect(eventLabel({ event_type: "offer" })).toBe("Status → Offer");
    expect(eventLabel({ event_type: "rejected" })).toBe("Status → Rejected");
    expect(eventLabel({ event_type: "withdrawn" })).toBe("Status → Withdrawn");
    expect(eventLabel({ event_type: "ghosted" })).toBe("Status → Ghosted");
  });

  it("labels every note-family event type", () => {
    expect(eventLabel({ event_type: "fit_judged" })).toBe("Fit judged");
    expect(eventLabel({ event_type: "artifact_saved" })).toBe("Artifact saved");
    expect(eventLabel({ event_type: "contact_added" })).toBe("Contact added");
    expect(eventLabel({ event_type: "outreach_sent" })).toBe("Outreach sent");
    expect(eventLabel({ event_type: "note" })).toBe("Note");
    expect(eventLabel({ event_type: "lesson" })).toBe("Lesson");
  });

  it("falls back to the raw event_type string for an unknown type", () => {
    expect(eventLabel({ event_type: "some_future_type" })).toBe("some_future_type");
  });
});

describe("whoLabel", () => {
  it("'web' -> {you, You}", () => {
    expect(whoLabel("web")).toEqual({ who: "you", name: "You" });
  });

  it("'token:foo' -> {agent, foo}", () => {
    expect(whoLabel("token:foo")).toEqual({ who: "agent", name: "foo" });
  });

  it("'token:claude-code' -> {agent, claude-code}", () => {
    expect(whoLabel("token:claude-code")).toEqual({ who: "agent", name: "claude-code" });
  });

  it("'agent:bar' -> {agent, bar}", () => {
    expect(whoLabel("agent:bar")).toEqual({ who: "agent", name: "bar" });
  });

  it("anything else -> {agent, recordedBy}", () => {
    expect(whoLabel("something-weird")).toEqual({ who: "agent", name: "something-weird" });
  });
});

// ci_scope drill 2026-09-11: a frontend-only change must take the frontend lane.
