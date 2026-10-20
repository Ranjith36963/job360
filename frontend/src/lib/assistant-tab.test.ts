import { describe, expect, it } from "vitest";
import { P_ABOVE, P_BELOW, P_CAP, P_OUTREACH_SEND, P_SCORE, P_EVERY, P_INBOX, ROUNDS, historyWhen, mergeHistory, valueText } from "./assistant-tab";
import { formatFeedTime } from "./home";

describe("valueText", () => {
  it("maps stored values to the labels on screen", () => {
    expect(valueText(P_ABOVE, "auto")).toBe("Apply on its own");
    expect(valueText(P_BELOW, "ask")).toBe("Ask me");
    expect(valueText(P_INBOX, "scheduled")).toBe("Auto");
    expect(valueText(P_INBOX, "declined")).toBe("Off");
    expect(valueText(P_EVERY, "6h")).toBe("every 6 hours");
    expect(valueText(P_EVERY, "1h")).toBe("every hour");
  });
  it("empty is 'no limit' for the daily limit and 'not set' elsewhere", () => {
    expect(valueText(P_CAP, null)).toBe("no limit");
    expect(valueText(P_SCORE, null)).toBe("not set");
  });
});

describe("outreach send", () => {
  it("valueText: only auto reads as on", () => {
    expect(valueText(P_OUTREACH_SEND, "auto")).toBe("on");
    expect(valueText(P_OUTREACH_SEND, "draft_only")).toBe("off");
    expect(valueText(P_OUTREACH_SEND, "")).toBe("off");
  });
  it("mergeHistory labels the path", () => {
    const m = mergeHistory({ [P_OUTREACH_SEND]: [{ set_by: "web", set_at: "2026-10-10T09:14:00Z", value: "auto" }] });
    expect(m.map((r) => `${r.label}|${r.now}`)).toEqual(["Automatic outreach emails|on"]);
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

describe("setup rounds", () => {
  it("targets come first", () => {
    expect(ROUNDS.map((r) => r.id)).toEqual(["targets", "you", "visa", "logistics", "equality", "settings"]);
  });
});

describe("new setting words", () => {
  it("valueText", () => {
    expect(valueText("assistant_settings.company_cap", null)).toBe("no limit");
    expect(valueText("assistant_settings.min_gap_minutes", 15)).toBe("15 minutes");
    expect(valueText("assistant_settings.apply_hours", { from: "09:00", to: "17:00" })).toBe("09:00 to 17:00");
    expect(valueText("assistant_settings.apply_hours", null)).toBe("any time");
  });
});
