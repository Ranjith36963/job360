import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

const live = vi.hoisted(() => ({ loaded: true, lastActiveAt: null as string | null, offline: false, paused: false }));
vi.mock("@/lib/live-changes", () => ({
  useAssistantActivity: () => ({ loaded: live.loaded, lastActiveAt: live.lastActiveAt, offline: live.offline }),
}));
vi.mock("@/lib/assistant-state", () => ({ usePause: () => ({ paused: live.paused }) }));

import { LiveLine, liveState, parseActive } from "./LiveLine";

// 2026-10-26 (a Monday) 14:02:00 UTC; the tests run with TZ unset, so pin the zone-free parts.
const NOW = Date.parse("2026-10-26T14:02:08Z");
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
  Object.assign(live, { loaded: true, lastActiveAt: null, offline: false, paused: false });
});
afterEach(() => vi.useRealTimers());

describe("liveState", () => {
  it("reads the backend's text timestamps, with or without a zone", () => {
    expect(parseActive("2026-10-26 14:02:00")).toBe(Date.parse("2026-10-26T14:02:00Z"));
    expect(parseActive("2026-10-26T14:02:00+00:00")).toBe(Date.parse("2026-10-26T14:02:00Z"));
  });

  it("working: active in the last 2 minutes, counting seconds", () => {
    expect(liveState(iso(8_000), NOW)).toEqual({ kind: "working", text: "Your assistant is working · 8s ago" });
    expect(liveState(iso(95_000), NOW).text).toBe("Your assistant is working · 1m 35s ago");
  });

  it("idle: within a day, the time it was last active", () => {
    const s = liveState(iso(3 * 60_000), NOW);
    expect(s.kind).toBe("idle");
    expect(s.text).toMatch(/^Your assistant was last active at \d\d:\d\d$/);
  });

  it("quiet: over a day, the weekday and time", () => {
    const s = liveState(iso(2 * 24 * 3_600_000), NOW);
    expect(s.kind).toBe("quiet");
    expect(s.text).toMatch(/^Your assistant was last active (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d:\d\d$/);
  });

  it("never: no assistant call at all; offline after failed polls", () => {
    expect(liveState(null, NOW)).toEqual({ kind: "never", text: "No assistant connected yet" });
    expect(liveState(iso(1_000), NOW, true).kind).toBe("offline");
  });
});

describe("LiveLine", () => {
  it("renders nothing until the first answer", () => {
    live.loaded = false;
    const { container } = render(<LiveLine />);
    expect(container.textContent).toBe("");
  });

  it("ticks the seconds while working", () => {
    live.lastActiveAt = iso(8_000);
    render(<LiveLine />);
    expect(screen.getByTestId("live-line")).toHaveTextContent("● Your assistant is working · 8s ago");
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByTestId("live-line")).toHaveTextContent("Your assistant is working · 11s ago");
  });

  it("never connected: links to Connect", () => {
    render(<LiveLine />);
    expect(screen.getByTestId("live-line")).toHaveAttribute("data-state", "never");
    expect(screen.getByRole("link", { name: "Connect" })).toHaveAttribute("href", "/connect");
  });

  it("adds Paused when the assistant is paused", () => {
    live.lastActiveAt = iso(10 * 60_000);
    live.paused = true;
    render(<LiveLine />);
    expect(screen.getByTestId("live-line").textContent).toMatch(/^Your assistant was last active at \d\d:\d\d · Paused$/);
  });

  it("never names a brand", () => {
    for (const at of [iso(1_000), iso(600_000), iso(3 * 86_400_000), null]) {
      live.lastActiveAt = at;
      const { container, unmount } = render(<LiveLine />);
      expect(container.textContent).not.toMatch(/claude|chatgpt|chrome/i);
      unmount();
    }
  });
});
