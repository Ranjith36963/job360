import { describe, expect, it } from "vitest";
import { hasStamp, readLastVisit, writeLastVisit } from "@/lib/home";
import { ALWAYS_ASKS, LAST_CHECK_KEY, LINE_SWITCH_LABEL, batchEdits, batchOn, batchText, belowText, SUBMIT_MODE_LABEL, lineSwitchText, pausedByText, quotaText, scoreLineText, sinceText } from "./morning-check";

const now = new Date("2026-10-10T12:00:00");

describe("morning-check words", () => {
  it("labels every closed setting value in plain words", () => {
    expect(LINE_SWITCH_LABEL).toEqual({ ask: "Ask me", auto: "Apply on its own", skip: "Drop it" });
    expect(scoreLineText(null)).toBe("Score line not set");
    expect(scoreLineText(80)).toBe("Score line 80");
    expect(lineSwitchText("auto", "ask")).toBe("Above: apply on its own · Below: ask me");
    expect(SUBMIT_MODE_LABEL).toEqual({ confirm: "Confirm before sending", auto_when_sure: "Send on its own when sure" });
  });

  it("quota, since and paused-by", () => {
    expect(quotaText(4, 10)).toBe("4 of 10 today");
    expect(quotaText(4, null)).toBe("4 today");
    expect(sinceText(null)).toBe("In the last 24 hours");
    expect(sinceText("2026-10-10T08:30:00", now)).toMatch(/^Since your last visit, \d{1,2}:\d{2}/);
    expect(pausedByText("web", "2026-10-10T09:40:00", now)).toMatch(/^Paused by you, \d{1,2}:\d{2}/);
    expect(pausedByText("token:Claude", "2026-10-10T09:40:00", now)).toMatch(/^Paused by Claude, /);
    expect(pausedByText("agent:Claude Code", "2026-10-10T09:40:00", now)).toMatch(/^Paused by Claude Code, /);
  });

  it("the stamp is keyed, never shared with Home", () => {
    window.localStorage.clear();
    const t = new Date("2026-10-10T12:00:00Z");
    expect(hasStamp(LAST_CHECK_KEY)).toBe(false);
    expect(readLastVisit(t, LAST_CHECK_KEY, 1)).toBe("2026-10-09T12:00:00.000Z");
    writeLastVisit("2026-10-10T08:00:00Z", LAST_CHECK_KEY);
    expect(hasStamp(LAST_CHECK_KEY)).toBe(true);
    expect(readLastVisit(t, LAST_CHECK_KEY, 1)).toBe("2026-10-10T08:00:00Z");
    expect(window.localStorage.getItem("job360-last-visit")).toBeNull();
    window.localStorage.clear();
  });
});

describe("batch auto apply (owner decision 2026-10-19)", () => {
  it("is on only when BOTH halves are on", () => {
    expect(batchOn("auto", "auto_when_sure")).toBe(true);
    expect(batchOn("auto", "confirm")).toBe(false);
    expect(batchOn("ask", "auto_when_sure")).toBe(false);
    expect(batchOn(undefined, undefined)).toBe(false);
    expect(batchText(true)).toBe("Batch auto apply: on");
    expect(batchText(false)).toBe("Batch auto apply: off");
  });

  it("says what happens below the line", () => {
    expect(belowText("skip")).toBe("Below the line: dropped");
    expect(belowText("auto")).toBe("Below the line: applies on its own");
    expect(belowText("ask")).toBe("Below the line: asks you");
    expect(belowText(null)).toBe("Below the line: asks you");
  });

  it("writes only what differs, and always records 'drop below the line'", () => {
    const s = { apply_min_score: 75, above_line: "ask", below_line: "skip", submit_mode: "confirm" };
    expect(batchEdits(s, { scoreValue: 75, batch: false })).toEqual([]);
    expect(batchEdits(s, { scoreValue: 80, batch: true })).toEqual([
      { path: "assistant_settings.apply_min_score", value: 80 },
      { path: "assistant_settings.above_line", value: "auto" },
      { path: "assistant_settings.submit_mode", value: "auto_when_sure" },
    ]);
    expect(batchEdits({ ...s, below_line: "ask" }, { scoreValue: 75, batch: false })).toEqual([
      { path: "assistant_settings.below_line", value: "skip" },
    ]);
    // an unset (empty) stored value reads as its default, so no needless write
    const unset = { apply_min_score: null, above_line: "", below_line: "skip", submit_mode: "" };
    expect(batchEdits(unset, { scoreValue: null, batch: false })).toEqual([]);
  });

  it("the amber note names every case it stops for", () => {
    for (const w of ["CAPTCHA", "login page", "guessed answer", "not verified"]) expect(ALWAYS_ASKS).toContain(w);
  });
});
