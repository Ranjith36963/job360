import { describe, expect, it } from "vitest";
import { hasStamp, readLastVisit, writeLastVisit } from "@/lib/home";
import { LAST_CHECK_KEY, LINE_SWITCH_LABEL, SUBMIT_MODE_LABEL, lineSwitchText, pausedByText, quotaText, scoreLineText, sinceText } from "./morning-check";

const now = new Date("2026-10-10T12:00:00");

describe("morning-check words", () => {
  it("labels every closed setting value in plain words", () => {
    expect(LINE_SWITCH_LABEL).toEqual({ ask: "Ask me", auto: "Apply on its own" });
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
