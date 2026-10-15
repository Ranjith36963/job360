import { describe, it, expect } from "vitest";
import { STATUS_LABEL, EVENT_LABEL, eventLabel, whoLabel } from "./event-labels";

// The closed sets from backend/src/core/settings.py, via a checked-in JSON that
// backend/tests/test_event_types_json_matches_settings.py pins to the settings
// tuples. A new backend event type fails that backend test until the JSON is
// regenerated, and then fails the label test below until it has a label.
import eventTypes from "./event-types.json";

const APPLICATION_STATUS_EVENT_TYPES: string[] = eventTypes.status;
const APPLICATION_NOTE_EVENT_TYPES: string[] = eventTypes.note;

describe("STATUS_LABEL", () => {
  it("has a label for every status a status event can produce", () => {
    // `brought` maps to `considering`, not itself.
    const statuses = APPLICATION_STATUS_EVENT_TYPES.map((t) => (t === "brought" ? "considering" : t));
    for (const status of statuses) {
      expect(STATUS_LABEL[status]).toBeTruthy();
    }
  });
});

describe("answered event label", () => {
  it("says it is the user's answer, via the assistant, when the payload has user_words", () => {
    const payload = { ask_id: 1, answer: "Two months", user_words: "two months" };
    expect(eventLabel({ event_type: "answered", payload, recorded_by: "agent:Claude" })).toBe(
      "Your answer (via Claude)",
    );
    expect(eventLabel({ event_type: "answered", payload, recorded_by: "token:agent" })).toBe(
      "Your answer (via agent)",
    );
  });
  it("keeps the plain label without user_words", () => {
    expect(
      eventLabel({ event_type: "answered", payload: { ask_id: 1, answer: "x" }, recorded_by: "web" }),
    ).toBe("You answered a question");
  });
});

describe("EVENT_LABEL", () => {
  it("has a label for every note-family event type", () => {
    for (const type of APPLICATION_NOTE_EVENT_TYPES) {
      expect(EVENT_LABEL[type]).toBeTruthy();
    }
  });
});

describe("event labels are plain words", () => {
  it("no note-family label is the raw event name", () => {
    for (const type of APPLICATION_NOTE_EVENT_TYPES) {
      expect(eventLabel({ event_type: type })).not.toBe(type);
      expect(eventLabel({ event_type: type })).not.toContain("_");
    }
  });
  it("labels the kit trail in the seeker's words", () => {
    expect(eventLabel({ event_type: "kit_read" })).toBe("Assistant opened your application kit");
    expect(eventLabel({ event_type: "form_swept" })).toBe("Form double-checked before sending");
    expect(eventLabel({ event_type: "submit_declined" })).toBe("You said don't send");
    expect(eventLabel({ event_type: "proof_text" })).toBe("Saved the confirmation page text");
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
    expect(eventLabel({ event_type: "artifact_saved" })).toBe("Document saved");
    expect(eventLabel({ event_type: "contact_added" })).toBe("Contact added");
    expect(eventLabel({ event_type: "outreach_sent" })).toBe("Outreach sent");
    expect(eventLabel({ event_type: "note" })).toBe("Note");
    expect(eventLabel({ event_type: "lesson" })).toBe("Lesson");
    expect(eventLabel({ event_type: "blocked" })).toBe("Assistant got stuck");
    expect(eventLabel({ event_type: "unblocked" })).toBe("Unblocked");
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
