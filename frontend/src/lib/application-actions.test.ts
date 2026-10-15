import { describe, it, expect } from "vitest";
import { ACTIONS_BY_STATUS, allowedActions, changesSinceApprovedCv, nothingSent, readyCardAllowed } from "./application-actions";

const BOTH = ["send_approve", "send_decline", "ready_card", "mark_applied"];

describe("allowedActions", () => {
  it("considering allows the send decision", () => {
    expect(allowedActions("considering")).toEqual(BOTH);
  });

  it.each([
    "applied", "replied", "interview_requested", "interview_scheduled", "interview_done",
    "offer", "rejected", "withdrawn", "ghosted",
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
    expect(Object.keys(ACTIONS_BY_STATUS)).toHaveLength(10);
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
  it("is null with no approval or an unknown approved CV", () => {
    expect(changesSinceApprovedCv([], [cv(10, 1)])).toBeNull();
    expect(changesSinceApprovedCv([approved(1, 99)], [cv(10, 1)])).toBeNull();
  });
});
