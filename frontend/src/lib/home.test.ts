import { afterEach, describe, expect, it, vi } from "vitest";
import {
  LAST_VISIT_KEY,
  buildSentence,
  dayAt,
  isBrandNew,
  lastApplied,
  waitsSentence,
  readLastVisit,
  selectFeed,
  sentenceText,
  writeLastVisit,
} from "@/lib/home";

const ev = (id: number, by: string, extra: Partial<Record<string, unknown>> = {}) =>
  ({
    id,
    application_id: 1,
    event_type: "artifact_saved",
    detail: "",
    payload: {},
    occurred_at: "2026-10-03T09:00:00Z",
    recorded_at: `2026-10-03T09:${String(id).padStart(2, "0")}:00Z`,
    recorded_by: by,
    corrects_event_id: null,
    scheduled_at: null,
    source: null,
    ...extra,
  }) as never;

const text = (args: Parameters<typeof buildSentence>[0]) => sentenceText(buildSentence(args));

describe("buildSentence", () => {
  it("zero records: the quiet line", () => {
    expect(text({ events: [], truncated: false, waits: null })).toBe("Nothing new since you were last here.");
  });

  it("web events are not counted", () => {
    expect(text({ events: [ev(1, "web"), ev(2, "web")], truncated: false, waits: null })).toBe(
      "Nothing new since you were last here."
    );
  });

  it("one assistant, one record (singular) and the name after agent:", () => {
    expect(text({ events: [ev(1, "agent:Claude")], truncated: false, waits: null })).toBe(
      "Your assistant (Claude) wrote 1 record since you were last here."
    );
  });

  it("one assistant, many records; token: names work too; web ignored", () => {
    const events = [ev(1, "token:Cursor"), ev(2, "token:Cursor"), ev(3, "web")];
    expect(text({ events, truncated: false, waits: null })).toBe(
      "Your assistant (Cursor) wrote 2 records since you were last here."
    );
  });

  it("several assistants: every one is named exactly as it signed, never a vague plural", () => {
    const events = [ev(1, "agent:Claude"), ev(2, "token:Cursor"), ev(3, "agent:Claude")];
    expect(text({ events, truncated: false, waits: null })).toBe(
      "Your assistants (Claude and Cursor) wrote 3 records since you were last here."
    );
  });

  it("three or more assistants are listed with commas, in first-seen order", () => {
    const events = [ev(1, "agent:Claude Code (job360)"), ev(2, "agent:Codex"), ev(3, "agent:Grok"), ev(4, "agent:Codex")];
    expect(text({ events, truncated: false, waits: null })).toBe(
      "Your assistants (Claude Code (job360), Codex and Grok) wrote 4 records since you were last here."
    );
  });

  it("highlights exactly the records phrase", () => {
    const parts = buildSentence({ events: [ev(1, "agent:Claude")], truncated: false, waits: null });
    expect(parts.filter((p) => p.em).map((p) => p.text)).toEqual(["1 record"]);
  });

  it("truncated shows N+", () => {
    expect(text({ events: [ev(1, "agent:Claude"), ev(2, "agent:Claude")], truncated: true, waits: null })).toBe(
      "Your assistant (Claude) wrote 2+ records since you were last here."
    );
  });

  it("appends who the jobs wait on: you, your PC, your assistant, and after the quiet line", () => {
    const one = [ev(1, "agent:Claude")];
    expect(text({ events: one, truncated: false, waits: { you: 1, your_pc: 0, assistant: 0 } })).toBe(
      "Your assistant (Claude) wrote 1 record since you were last here. 1 job waits on you"
    );
    expect(text({ events: one, truncated: false, waits: { you: 5, your_pc: 2, assistant: 1 } })).toBe(
      "Your assistant (Claude) wrote 1 record since you were last here. 5 jobs wait on you · 2 on your PC · 1 on your assistant"
    );
    expect(text({ events: [], truncated: false, waits: { you: 3, your_pc: 0, assistant: 4 } })).toBe(
      "Nothing new since you were last here. 3 jobs wait on you · 4 on your assistant"
    );
  });
});

describe("waitsSentence", () => {
  it("omits zero parts, and says Nothing waits on you when no job waits on the user", () => {
    expect(waitsSentence({ you: 2, your_pc: 0, assistant: 0 })).toBe("2 jobs wait on you");
    expect(waitsSentence({ you: 0, your_pc: 3, assistant: 2 })).toBe("Nothing waits on you");
  });
});

describe("last visit storage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });
  const now = new Date("2026-10-04T12:00:00Z");

  it("absent → 7 days ago", () => {
    expect(readLastVisit(now)).toBe("2026-09-27T12:00:00.000Z");
  });

  it("present → the stored value; garbage → 7 days ago", () => {
    window.localStorage.setItem(LAST_VISIT_KEY, "2026-10-03T08:00:00Z");
    expect(readLastVisit(now)).toBe("2026-10-03T08:00:00Z");
    window.localStorage.setItem(LAST_VISIT_KEY, "not a date");
    expect(readLastVisit(now)).toBe("2026-09-27T12:00:00.000Z");
  });

  it("write stores only the timestamp under the one key", () => {
    writeLastVisit("2026-10-04T12:00:00Z");
    expect(window.localStorage.getItem(LAST_VISIT_KEY)).toBe("2026-10-04T12:00:00Z");
    expect(window.localStorage.length).toBe(1);
  });

  it("storage that throws never throws out", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readLastVisit(now)).toBe("2026-09-27T12:00:00.000Z");
    expect(() => writeLastVisit("2026-10-04T12:00:00Z")).not.toThrow();
  });
});

describe("selectFeed", () => {
  const apps = [{ id: 1, job_company: "Mistral AI", job_title: "Engineer", last_event_at: null, status: "applied" }];

  it("takes the last 12 assistant events, newest first, web excluded", () => {
    const events = [];
    for (let i = 1; i <= 20; i++) events.push(ev(i, i % 5 === 0 ? "web" : "agent:Claude"));
    const feed = selectFeed(events, apps);
    expect(feed).toHaveLength(12);
    const ids = feed.map((f) => f.id);
    expect(ids[0]).toBe(19);
    expect([...ids].sort((a, b) => b - a)).toEqual(ids);
    expect(ids).not.toContain(20);
    expect(ids).not.toContain(15);
    expect(feed[0].company).toBe("Mistral AI");
    expect(feed[0].assistant).toBe("Claude");
  });

  it("uses the event label, and adds the detail when there is one", () => {
    const feed = selectFeed(
      [ev(1, "agent:Claude", { detail: "CV v3" }), ev(2, "agent:Claude", { event_type: "mystery" })],
      apps
    );
    expect(feed[1].line).toBe("Document saved: CV v3");
    expect(feed[0].line).toBe("mystery");
  });

  it("empty when only the user wrote", () => {
    expect(selectFeed([ev(1, "web")], apps)).toEqual([]);
  });
});

describe("isBrandNew", () => {
  it("only when all three are known to be zero", () => {
    expect(isBrandNew({ applicationsTotal: 0, eventCount: 0, openAsks: 0 })).toBe(true);
    expect(isBrandNew({ applicationsTotal: 0, eventCount: 2, openAsks: 0 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: 3, eventCount: 0, openAsks: 0 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: null, eventCount: 0, openAsks: 0 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: 0, eventCount: null, openAsks: 0 })).toBe(false);
  });

  it("never hides a waiting question behind the welcome", () => {
    // A general ask (no application) writes no event, so the feed stays empty.
    expect(isBrandNew({ applicationsTotal: 0, eventCount: 0, openAsks: 1 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: 0, eventCount: 0, openAsks: null })).toBe(false);
  });
});

describe("lastApplied", () => {
  const apps = [
    { id: 1, job_title: "Engineer", job_company: "Monzo" },
    { id: 2, job_title: "Analyst", job_company: "" },
  ] as never;
  it("picks the newest applied event by occurred_at and names its company", () => {
    const events = [
      ev(1, "agent:Claude", { event_type: "applied", occurred_at: "2026-10-05T10:00:00Z" }),
      ev(2, "agent:Claude", { event_type: "applied", occurred_at: "2026-10-01T10:00:00Z", application_id: 2 }),
      ev(3, "agent:Claude", { event_type: "replied", occurred_at: "2026-10-09T10:00:00Z" }),
    ];
    expect(lastApplied(events, apps)).toEqual({ company: "Monzo", occurredAt: "2026-10-05T10:00:00Z" });
  });
  it("falls back to the job title, skips unparseable dates, null when none", () => {
    const events = [
      ev(1, "agent:Claude", { event_type: "applied", occurred_at: "last week" }),
      ev(2, "agent:Claude", { event_type: "applied", occurred_at: "2026-10-01", application_id: 2 }),
    ];
    expect(lastApplied(events, apps)).toEqual({ company: "Analyst", occurredAt: "2026-10-01" });
    expect(lastApplied([ev(1, "agent:Claude")], apps)).toBeNull();
  });
});

describe("dayAt", () => {
  const now = new Date(2026, 9, 22, 15, 0);
  it("today / yesterday / weekday / date, with the clock unless only a date was given", () => {
    expect(dayAt(new Date(2026, 9, 22, 14, 5).toISOString(), now)).toMatch(/^today at /);
    expect(dayAt(new Date(2026, 9, 21, 9, 30).toISOString(), now)).toMatch(/^yesterday at /);
    expect(dayAt("2026-10-19", now)).toMatch(/^on \S+$/);
    expect(dayAt("2026-09-01", now)).toMatch(/^on /);
    expect(dayAt("2026-09-01", now)).not.toMatch(/ at /);
    expect(dayAt("not a date", now)).toBe("");
  });
});

describe("process events stay out of Activity", () => {
  it("selectFeed and buildSentence skip process events", () => {
    const events = [ev(1, "agent:Claude"), ev(2, "agent:Claude", { event_type: "process_log", process: true })];
    const apps = [{ id: 1, job_company: "Acme", job_title: "Dev" }] as never;
    expect(selectFeed(events, apps)).toHaveLength(1);
    const text = sentenceText(buildSentence({ events, truncated: false, waits: null }));
    expect(text).toContain("wrote 1 record ");
  });
});
