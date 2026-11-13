import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ whatsNew: vi.fn(), listApplications: vi.fn() }));
vi.mock("@/lib/api", () => api);

import { fetchNewestWhatsNew } from "@/lib/home-fetch";
import { FEED_LIMIT, FEED_NEWEST_MAX_PAGES, assistantEvents } from "@/lib/home";

const ev = (id: number, by = "agent:Claude") => ({
  id, application_id: 7, event_type: "note", detail: "", payload: {}, occurred_at: "2026-11-01T00:00:00Z",
  recorded_at: "2026-11-01T00:00:00Z", recorded_by: by, corrects_event_id: null, scheduled_at: null, source: null,
});
const page = (events: ReturnType<typeof ev>[], truncated: boolean) => ({
  now: "2026-11-13T00:00:00Z", since: "x", events, applications: [{ id: 7, job_title: "E", job_company: "C" }],
  next_since: "x", next_after_id: null, truncated,
  next_before_id: events.length ? Math.min(...events.map((e) => e.id)) : null,
});
const rows = (events: readonly unknown[]) => assistantEvents(events as ReturnType<typeof ev>[]).length;
const enough = (events: readonly unknown[]) => rows(events) >= FEED_LIMIT;

beforeEach(() => vi.clearAllMocks());

describe("fetchNewestWhatsNew", () => {
  it("makes ONE newest call when the first page already holds enough rows", async () => {
    const first = Array.from({ length: 200 }, (_, i) => ev(1000 + i));
    api.whatsNew.mockResolvedValueOnce(page(first, true));
    const r = await fetchNewestWhatsNew("S", enough);
    expect(api.whatsNew).toHaveBeenCalledTimes(1);
    expect(api.whatsNew).toHaveBeenCalledWith({ since: "S", limit: 200, newest: true, before_id: undefined });
    expect(r.events).toHaveLength(200);
    expect(r.truncated).toBe(true);
    expect(r.now).toBe("2026-11-13T00:00:00Z");
  });

  it("follows next_before_id, at most 3 pages, keeping events oldest first", async () => {
    // pages of web-only events never build a feed row, so the fetch keeps going until the cap
    api.whatsNew
      .mockResolvedValueOnce(page([ev(30, "web"), ev(31, "web")], true))
      .mockResolvedValueOnce(page([ev(20, "web"), ev(21, "web")], true))
      .mockResolvedValueOnce(page([ev(10, "web"), ev(11, "web")], true));
    const r = await fetchNewestWhatsNew("S", enough);
    expect(api.whatsNew).toHaveBeenCalledTimes(FEED_NEWEST_MAX_PAGES);
    expect(api.whatsNew.mock.calls.map((c) => c[0].before_id)).toEqual([undefined, 30, 20]);
    expect(r.events.map((e) => e.id)).toEqual([10, 11, 20, 21, 30, 31]);
    expect(r.truncated).toBe(true);
  });

  it("stops when the older events run out, even with too few rows", async () => {
    api.whatsNew.mockResolvedValueOnce(page([ev(5), ev(6)], false));
    const r = await fetchNewestWhatsNew("S", enough);
    expect(api.whatsNew).toHaveBeenCalledTimes(1);
    expect(r.truncated).toBe(false);
  });

  it("stops after the second page once enough rows are in hand", async () => {
    api.whatsNew
      .mockResolvedValueOnce(page([ev(50), ev(51)], true))
      .mockResolvedValueOnce(page(Array.from({ length: 10 }, (_, i) => ev(40 + i)), true));
    const r = await fetchNewestWhatsNew("S", enough);
    expect(api.whatsNew).toHaveBeenCalledTimes(2);
    expect(r.events).toHaveLength(12);
  });
});
