import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Both shared readers keep a settled SUCCESSFUL answer for a short window per key, so a consumer that
// asks a moment later (the badge and the pause hook wait for the signed-in user) shares the same GET.

const api = vi.hoisted(() => ({ getNeedsYouCount: vi.fn(), getAssistantSettings: vi.fn(), updateProfileFields: vi.fn() }));
vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { fetchNeedsYouCountShared } from "@/lib/needs-you-count";
import { fetchSettingsShared } from "@/lib/assistant-state";
import { SHARED_READ_TTL_MS } from "@/lib/shared-read";

const readers = [
  { name: "needs-you-count", read: fetchNeedsYouCountShared, fn: api.getNeedsYouCount },
  { name: "assistant-settings", read: fetchSettingsShared, fn: api.getAssistantSettings },
] as const;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-13T10:00:00Z"));
  api.getNeedsYouCount.mockReset().mockResolvedValue({ n: 1 });
  api.getAssistantSettings.mockReset().mockResolvedValue({ paused: false });
});
afterEach(() => vi.useRealTimers());

// Each case uses its own key, so one case never leaves a kept answer for the next.
describe.each(readers)("$name reader", ({ read, fn, name }) => {
  it("a second ask for the same key after the first settled, inside the window, reuses the answer", async () => {
    await read(`${name}:late`);
    await vi.advanceTimersByTimeAsync(50);
    await read(`${name}:late`);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("after the window it reads again", async () => {
    await read(`${name}:expire`);
    await vi.advanceTimersByTimeAsync(SHARED_READ_TTL_MS + 1);
    await read(`${name}:expire`);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("a different key inside the window always reads again, and replaces the kept answer", async () => {
    await read(`${name}:a`);
    await read(`${name}:b`);
    expect(fn).toHaveBeenCalledTimes(2);
    await read(`${name}:a`); // `a` was replaced by `b`: fresh again
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("a rejected read is never kept", async () => {
    fn.mockRejectedValueOnce(new Error("down"));
    await expect(read(`${name}:fail`)).rejects.toThrow("down");
    await read(`${name}:fail`);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
