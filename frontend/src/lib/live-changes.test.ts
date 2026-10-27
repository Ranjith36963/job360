import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CHANGES_EVENT, ERROR_MS, POLL_MS, THROTTLE_MS, startLiveChanges } from "./live-changes";
import type { ChangesDetail } from "./live-changes";

// The one live poller (PR 6): fake timers, a mocked fetch, a settable tab visibility.

const areas = (n: number, asks = `k${n}`) => ({
  assistant: `a${n}`,
  asks,
  ready: "r0",
  applications: "p0",
  settings: "s0",
  other: "o0",
});
const body = (n: number, asks?: string) =>
  new Response(JSON.stringify({ cursor: `c${n}`, assistant_last_active_at: "2026-10-26 14:02:00", areas: areas(n, asks) }), { status: 200 });

let visibility: DocumentVisibilityState = "visible";
let events: { at: number; d: ChangesDetail }[] = [];
const onEvent = (e: Event) => events.push({ at: Date.now(), d: (e as CustomEvent<ChangesDetail>).detail });
let stop: () => void = () => {};

function setVisible(v: DocumentVisibilityState) {
  visibility = v;
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers({ now: 0 });
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  events = [];
  window.addEventListener(CHANGES_EVENT, onEvent);
});
afterEach(() => {
  stop();
  window.removeEventListener(CHANGES_EVENT, onEvent);
  vi.useRealTimers();
});

describe("live-changes poller", () => {
  it("polls at once, then every 5 s, sending the last cursor as If-None-Match", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(body(1)).mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].headers).toEqual({});
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/changes");
    expect(fetchMock.mock.calls[1][1].headers).toEqual({ "If-None-Match": '"c1"' });
    await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("a 304 fires nothing and keeps polling", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(body(1)).mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(POLL_MS * 4);
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(events).toEqual([]);
  });

  it("stops while the tab is hidden and polls at once, with a full refetch, when it shows again", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(body(1)).mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(0);
    setVisible("hidden");
    await vi.advanceTimersByTimeAsync(POLL_MS * 20);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(events).toEqual([]);
    setVisible("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(events.map((e) => e.d.full)).toEqual([true]);
  });

  it("focus polls at once and always fires a full refetch; a focus + visible pair fires once", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(body(1)).mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(1_000);
    window.dispatchEvent(new Event("focus"));
    setVisible("visible");
    await vi.advanceTimersByTimeAsync(0);
    expect(events).toHaveLength(1);
    expect(events[0].d).toMatchObject({ full: true, areas: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("a moved cursor fires the event naming the areas that moved; assistant alone means refetch all", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(body(1))
      .mockResolvedValueOnce(body(2))
      .mockResolvedValueOnce(body(3, "k2")) // only the assistant marker moved
      .mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(0);
    expect(events).toEqual([]); // the first answer is the baseline, not a change
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(events).toHaveLength(1);
    expect(events[0].d).toMatchObject({ full: false, areas: ["assistant", "asks"] });
    await vi.advanceTimersByTimeAsync(THROTTLE_MS);
    expect(events).toHaveLength(2);
    expect(events[1].d).toMatchObject({ full: true });
  });

  it("throttles a burst to at most one event per 10 s, leading and trailing, and loses no change", async () => {
    let n = 0;
    const fetchMock = vi.fn().mockImplementation(async () => (n < 8 ? body(++n) : new Response(null, { status: 304 })));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(POLL_MS * 12);
    expect(n).toBe(8); // seven moves after the baseline, one every 5 s
    expect(events.length).toBeGreaterThanOrEqual(2);
    expect(events.length).toBeLessThanOrEqual(5);
    expect(events[0].at).toBe(POLL_MS); // leading edge: the first change is not delayed
    for (let i = 1; i < events.length; i++) expect(events[i].at - events[i - 1].at).toBeGreaterThanOrEqual(THROTTLE_MS);
    // The trailing event comes after the last change, so the page ends on the newest state.
    expect(events[events.length - 1].at).toBeGreaterThanOrEqual(POLL_MS * 7);
  });

  it("backs off to 60 s after an error, then returns to 5 s", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("boom", { status: 500 }))
      .mockResolvedValueOnce(body(1))
      .mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(ERROR_MS - 1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("stops for good on 401, even on focus", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(ERROR_MS * 3);
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(events).toEqual([]);
  });

  it("the stop function ends polling", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 304 }));
    stop = startLiveChanges(fetchMock);
    await vi.advanceTimersByTimeAsync(0);
    stop();
    await vi.advanceTimersByTimeAsync(POLL_MS * 10);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
