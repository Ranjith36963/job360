import { describe, expect, it } from "vitest";
import { P_APPLY, P_CAP, P_EVERY, P_INBOX, historyWhen, mergeHistory, valueText } from "./assistant-tab";
import { formatFeedTime } from "./home";

describe("valueText", () => {
  it("maps stored values to the labels on screen", () => {
    expect(valueText(P_APPLY, "selective_above_score")).toBe("Only above my score line");
    expect(valueText(P_INBOX, "scheduled")).toBe("Auto");
    expect(valueText(P_INBOX, "declined")).toBe("Off");
    expect(valueText(P_EVERY, "6h")).toBe("every 6 hours");
  });
  it("empty is 'no limit' for the daily limit and 'not set' elsewhere", () => {
    expect(valueText(P_CAP, null)).toBe("no limit");
    expect(valueText(P_APPLY, "")).toBe("not set");
  });
});

describe("mergeHistory", () => {
  const rows = {
    [P_CAP]: [
      { set_by: "web", set_at: "2026-10-10T09:14:00Z", value: 10 },
      { set_by: "agent:Claude", set_at: "2026-10-08T08:00:00Z", value: null },
    ],
    [P_EVERY]: [
      { set_by: "agent:Claude Code", set_at: "2026-10-09T21:02:00Z", value: "6h" },
      { set_by: "web", set_at: "2026-10-01T10:00:00Z", value: "12h" },
    ],
  };
  it("merges newest first with exact assistant names and was -> now", () => {
    const m = mergeHistory(rows);
    expect(m.map((r) => `${r.by}|${r.label}|${r.was}|${r.now}`)).toEqual([
      "You|Daily limit|no limit|10",
      "Claude Code|Inbox check|every 12 hours|every 6 hours",
      "Claude|Daily limit|no limit|no limit",
      "You|Inbox check|not set|every 12 hours",
    ]);
  });
  it("only the newest change of a setting can be taken back, and it carries the previous value", () => {
    const m = mergeHistory(rows);
    expect(m.filter((r) => r.newest).map((r) => [r.path, r.previous])).toEqual([
      [P_CAP, null],
      [P_EVERY, "12h"],
    ]);
  });
});

describe("historyWhen", () => {
  it("'Today' for today, the feed time otherwise", () => {
    const now = new Date("2026-10-10T12:00:00");
    const today = new Date("2026-10-10T09:14:00").toISOString();
    const older = new Date("2026-10-04T21:02:00").toISOString();
    expect(historyWhen(today, now)).toBe(`Today ${formatFeedTime(today, now)}`);
    expect(historyWhen(older, now)).toBe(formatFeedTime(older, now));
    expect(historyWhen("nope", now)).toBe("");
  });
});
