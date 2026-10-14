import { describe, expect, it } from "vitest";
import { eventTitle } from "@/lib/event-detail";
import { feedLine } from "@/lib/home";

describe("eventTitle", () => {
  it("names the document kind and version", () => {
    expect(eventTitle({ event_type: "artifact_saved", payload: { kind: "cv", version_no: 2 } })).toBe("CV v2 saved");
    expect(eventTitle({ event_type: "artifact_saved", payload: { kind: "cover_letter", version_no: 1 } })).toBe(
      "Cover letter v1 saved",
    );
    expect(eventTitle({ event_type: "artifact_saved", payload: { kind: "outreach", version_no: 3 } })).toBe(
      "Outreach message saved",
    );
  });

  it("falls back to the plain label when the payload names no kind", () => {
    expect(eventTitle({ event_type: "artifact_saved", payload: {} })).toBe("Document saved");
    expect(eventTitle({ event_type: "artifact_saved" })).toBe("Document saved");
  });

  it("adds the contact name to outreach events only when the payload has it", () => {
    expect(eventTitle({ event_type: "outreach_sent", payload: { contact_id: 4, contact_name: "Jordan Lee" } })).toBe(
      "Outreach sent · Jordan Lee",
    );
    expect(eventTitle({ event_type: "outreach_sent", payload: { contact_id: 4 } })).toBe("Outreach sent");
  });
});

describe("feedLine", () => {
  it("uses the specific title, then the detail", () => {
    expect(
      feedLine({ event_type: "artifact_saved", detail: "tuned for Mistral", payload: { kind: "cv", version_no: 2 } }),
    ).toBe("CV v2 saved: tuned for Mistral");
  });
});
