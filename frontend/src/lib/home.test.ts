import { afterEach, describe, expect, it, vi } from "vitest";
import {
  LAST_VISIT_KEY,
  buildSentence,
  isBrandNew,
  needsYouSentence,
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
    expect(text({ events: [], truncated: false, openAsks: 0 })).toBe("Nothing new since you were last here.");
  });

  it("web events are not counted", () => {
    expect(text({ events: [ev(1, "web"), ev(2, "web")], truncated: false, openAsks: 0 })).toBe(
      "Nothing new since you were last here."
    );
  });

  it("one assistant, one record (singular) and the name after agent:", () => {
    expect(text({ events: [ev(1, "agent:Claude")], truncated: false, openAsks: 0 })).toBe(
      "Claude wrote 1 record since you were last here."
    );
  });

  it("one assistant, many records; token: names work too; web ignored", () => {
    const events = [ev(1, "token:Cursor"), ev(2, "token:Cursor"), ev(3, "web")];
    expect(text({ events, truncated: false, openAsks: 0 })).toBe(
      "Cursor wrote 2 records since you were last here."
    );
  });

  it("several assistants", () => {
    const events = [ev(1, "agent:Claude"), ev(2, "token:Cursor"), ev(3, "agent:Claude")];
    expect(text({ events, truncated: false, openAsks: 0 })).toBe(
      "Your assistants wrote 3 records since you were last here."
    );
  });

  it("highlights exactly the records phrase", () => {
    const parts = buildSentence({ events: [ev(1, "agent:Claude")], truncated: false, openAsks: 2 });
    expect(parts.filter((p) => p.em).map((p) => p.text)).toEqual(["1 record"]);
  });

  it("truncated shows N+", () => {
    expect(text({ events: [ev(1, "agent:Claude"), ev(2, "agent:Claude")], truncated: true, openAsks: 0 })).toBe(
      "Claude wrote 2+ records since you were last here."
    );
  });

  it("appends the needs-you sentence: 0, 1, many, and after the quiet line", () => {
    const one = [ev(1, "agent:Claude")];
    expect(text({ events: one, truncated: false, openAsks: 1 })).toBe(
      "Claude wrote 1 record since you were last here. One thing needs you."
    );
    expect(text({ events: one, truncated: false, openAsks: 2 })).toBe(
      "Claude wrote 1 record since you were last here. Two things need you."
    );
    expect(text({ events: [], truncated: false, openAsks: 3 })).toBe(
      "Nothing new since you were last here. Three things need you."
    );
  });
});

describe("needsYouSentence", () => {
  it("words up to ten, digits after, silent at zero", () => {
    expect(needsYouSentence(0)).toBe("");
    expect(needsYouSentence(10)).toBe("Ten things need you.");
    expect(needsYouSentence(11)).toBe("11 things need you.");
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
  it("only when both are known to be zero", () => {
    expect(isBrandNew({ applicationsTotal: 0, eventCount: 0 })).toBe(true);
    expect(isBrandNew({ applicationsTotal: 0, eventCount: 2 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: 3, eventCount: 0 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: null, eventCount: 0 })).toBe(false);
    expect(isBrandNew({ applicationsTotal: 0, eventCount: null })).toBe(false);
  });
});
