import { describe, it, expect } from "vitest";
import { ACTIONS_BY_STATUS, allowedActions, changesSinceApprovedCv, isQueued, nothingSent, readyCardAllowed, submitUnconfirmed } from "./application-actions";

const BOTH = ["send_approve", "send_decline", "ready_card", "mark_applied"];

describe("allowedActions", () => {
  it("considering allows the send decision", () => {
    expect(allowedActions("considering")).toEqual(BOTH);
  });

  it.each([
    "applied", "replied", "interview_requested", "interview_scheduled", "interview_done",
    "offer", "hired", "offer_declined", "rejected", "withdrawn", "ghosted",
  ])("%s allows no send decision", (status) => {
    expect(allowedActions(status)).toEqual(["mark_applied"]);
    expect(readyCardAllowed(status)).toBe(false);
  });

  it("an unknown status allows nothing", () => {
    expect(allowedActions("something_new")).toEqual([]);
    expect(allowedActions("")).toEqual([]);
  });

  it("keeps the component gates: no CV or a newer fill waiting hides the pair", () => {
    expect(allowedActions("considering", { hasCv: false })).toEqual(["ready_card", "mark_applied"]);
    expect(allowedActions("considering", { newFillWaiting: true })).toEqual(["ready_card", "mark_applied"]);
    expect(allowedActions("considering", { hasCv: true, newFillWaiting: false })).toEqual(BOTH);
  });

  it("the Ready card shows only while considering", () => {
    expect(readyCardAllowed("considering")).toBe(true);
  });
});

describe("the table drives every button", () => {
  it("considering allows all four; later statuses only Mark Applied", () => {
    expect(allowedActions("considering")).toEqual(["send_approve", "send_decline", "ready_card", "mark_applied"]);
    expect(allowedActions("applied")).toEqual(["mark_applied"]);
    expect(Object.keys(ACTIONS_BY_STATUS)).toHaveLength(12);
  });

  it("Mark Applied hides once a receipt exists, in any status", () => {
    expect(allowedActions("considering", { hasReceipt: true })).not.toContain("mark_applied");
    expect(allowedActions("applied", { hasReceipt: true })).toEqual([]);
  });

  it("nothingSent follows the same row as the Ready card", () => {
    expect(nothingSent("considering")).toBe(true);
    expect(nothingSent("applied")).toBe(false);
    expect(nothingSent("mystery")).toBe(false);
  });
});

describe("changesSinceApprovedCv", () => {
  const cv = (id: number, version_no: number) => ({ id, kind: "cv", version_no });
  const approved = (id: number, artifact_id: number) => ({ id, event_type: "submit_approved", payload: { artifact_id } });

  it("counts CV versions saved after the approved one", () => {
    expect(changesSinceApprovedCv([approved(1, 11)], [cv(10, 1), cv(11, 2), cv(12, 3), cv(13, 4)])).toBe(2);
  });
  it("is 0 when the approved CV is still the newest", () => {
    expect(changesSinceApprovedCv([approved(1, 11)], [cv(10, 1), cv(11, 2)])).toBe(0);
  });
  it("uses the newest approval", () => {
    expect(changesSinceApprovedCv([approved(1, 10), approved(5, 12)], [cv(10, 1), cv(12, 3), cv(13, 4)])).toBe(1);
  });
  it("never approved: counts versions above the first", () => {
    expect(changesSinceApprovedCv([], [cv(10, 1), cv(11, 2), cv(12, 3)])).toBe(2);
    expect(changesSinceApprovedCv([approved(1, 99)], [cv(10, 1), cv(11, 2)])).toBe(1);
    expect(changesSinceApprovedCv([], [])).toBe(0);
  });
});

describe("submitUnconfirmed / isQueued", () => {
  const ev = (id: number, event_type: string, extra: Record<string, unknown> = {}) => ({ id, event_type, payload: {}, ...extra });

  it("is true when the newest of attempted / applied / resolved is submit_attempted", () => {
    expect(submitUnconfirmed([ev(1, "submit_attempted")])).toBe(true);
    expect(submitUnconfirmed([ev(1, "submit_attempted"), ev(2, "applied")])).toBe(false);
    expect(submitUnconfirmed([ev(1, "submit_attempted"), ev(2, "submit_resolved")])).toBe(false);
    expect(submitUnconfirmed([ev(1, "applied"), ev(2, "submit_attempted")])).toBe(true);
    expect(submitUnconfirmed([ev(1, "note")])).toBe(false);
  });
  it("a retracted applied resolves nothing", () => {
    expect(submitUnconfirmed([ev(1, "submit_attempted"), ev(2, "applied"), ev(3, "note", { corrects_event_id: 2 })])).toBe(true);
  });
  it("is on the queue when the newest surviving queue event is queued", () => {
    expect(isQueued([ev(1, "queued")])).toBe(true);
    expect(isQueued([ev(1, "queued"), ev(2, "unqueued")])).toBe(false);
    expect(isQueued([ev(1, "queued"), ev(2, "unqueued", { superseded: true })])).toBe(true);
    expect(isQueued([])).toBe(false);
  });
});
