import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApprovalTag } from "./ApprovalTag";
import { Timeline } from "./Timeline";

const base = { at: "2026-11-13T10:00:00Z", ref: 5, ref_kind: "event", reviewer: null };

describe("ApprovalTag", () => {
  it("says AI reviewed for an agent record, with the reviewer as cut-short text", () => {
    const evil = "<img src=x onerror=alert(1)>" + "x".repeat(60);
    const { container } = render(<ApprovalTag approval={{ ...base, by: "ai", what: "cv", reviewer: evil }} />);
    const tag = screen.getByTestId("approval-tag");
    expect(tag).toHaveAttribute("data-by", "ai");
    expect(tag.textContent).toBe(`AI reviewed · CV · ${evil.slice(0, 30)}`);
    expect(container.querySelector("img")).toBeNull();
  });

  it("says You approved for the owner's own record", () => {
    render(<ApprovalTag approval={{ ...base, by: "you", what: "submit" }} />);
    expect(screen.getByTestId("approval-tag").textContent).toBe("You approved · Submit");
  });

  it("shows nothing without an approval or with an unknown one", () => {
    const { container, rerender } = render(<ApprovalTag approval={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ApprovalTag approval={{ ...base, by: "robot", what: "cv" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is tagged on the timeline event it belongs to and only there", () => {
    const ev = (id: number) => ({
      id, event_type: "applied", detail: "", payload: {}, occurred_at: "2026-11-13T10:00:00Z",
      recorded_at: "2026-11-13T10:00:00Z", recorded_by: "token:x", corrects_event_id: null, superseded: false,
      source: null, scheduled_at: null, process: false,
    });
    render(<Timeline events={[ev(5), ev(6)]} approvals={[{ ...base, by: "ai", what: "submit", ref: 6 }]} />);
    const rows = screen.getAllByTestId("timeline-event");
    expect(rows[0].querySelector("[data-testid=approval-tag]")).toBeNull();
    expect(rows[1].querySelector("[data-testid=approval-tag]")?.textContent).toBe("AI reviewed · Submit");
  });
});
