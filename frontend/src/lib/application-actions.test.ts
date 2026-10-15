import { describe, it, expect } from "vitest";
import { allowedActions, readyCardAllowed } from "./application-actions";

const BOTH = ["send_approve", "send_decline"];

describe("allowedActions", () => {
  it("considering allows the send decision", () => {
    expect(allowedActions("considering")).toEqual(BOTH);
  });

  it.each([
    "applied", "replied", "interview_requested", "interview_scheduled", "interview_done",
    "offer", "rejected", "withdrawn", "ghosted",
  ])("%s allows no send decision", (status) => {
    expect(allowedActions(status)).toEqual([]);
    expect(readyCardAllowed(status)).toBe(false);
  });

  it("an unknown status allows nothing", () => {
    expect(allowedActions("something_new")).toEqual([]);
    expect(allowedActions("")).toEqual([]);
  });

  it("keeps the component gates: no CV or a newer fill waiting hides the pair", () => {
    expect(allowedActions("considering", { hasCv: false })).toEqual([]);
    expect(allowedActions("considering", { newFillWaiting: true })).toEqual([]);
    expect(allowedActions("considering", { hasCv: true, newFillWaiting: false })).toEqual(BOTH);
  });

  it("the Ready card shows only while considering", () => {
    expect(readyCardAllowed("considering")).toBe(true);
  });
});
