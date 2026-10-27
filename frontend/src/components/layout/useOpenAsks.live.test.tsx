import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { CHANGES_EVENT } from "@/lib/live-changes";
import { useOpenAsks } from "./useOpenAsks";

// The badge, live: off Needs you it shows the server's exact total (GET /api/needs-you-count) and
// re-reads it on job360:changes. On Needs you the page re-reads on the same event and announces its
// own total (the same number), so the hook reads nothing there.

const getNeedsYouCount = vi.fn();
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getNeedsYouCount: (...a: unknown[]) => getNeedsYouCount(...a),
}));

const total = (n: number) => ({ n, jobs: n, other: 0 });
const changed = () =>
  act(() => {
    window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { areas: ["asks"], full: false, seq: Math.random() } }));
  });

beforeEach(() => {
  getNeedsYouCount.mockReset().mockResolvedValue(total(1));
});

describe("useOpenAsks live", () => {
  it("shows the endpoint's number off Needs you, and re-counts quietly when the assistant changes something", async () => {
    const { result } = renderHook(() => useOpenAsks(true, "/"));
    await waitFor(() => expect(result.current).toBe(1));
    getNeedsYouCount.mockResolvedValue(total(2));
    changed();
    await waitFor(() => expect(result.current).toBe(2));
  });

  it("a failed live re-count keeps the badge as it was", async () => {
    const { result } = renderHook(() => useOpenAsks(true, "/profile"));
    await waitFor(() => expect(result.current).toBe(1));
    getNeedsYouCount.mockRejectedValue(new Error("down"));
    changed();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(result.current).toBe(1);
  });

  it("does not read the endpoint on Needs you: the page announces its own total", async () => {
    const { result } = renderHook(() => useOpenAsks(true, "/needs-you"));
    changed();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(getNeedsYouCount).not.toHaveBeenCalled();
    act(() => {
      window.dispatchEvent(new CustomEvent("job360:asks-changed", { detail: 7 }));
    });
    expect(result.current).toBe(7);
  });

  it("reads nothing when signed out", async () => {
    const { result } = renderHook(() => useOpenAsks(false, "/"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(getNeedsYouCount).not.toHaveBeenCalled();
    expect(result.current).toBe(0);
  });
});
