import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { CHANGES_EVENT } from "@/lib/live-changes";
import { useOpenAsks } from "./useOpenAsks";

// The badge, live (PR 6): it re-counts on job360:changes, except on Needs you, where the page
// re-reads on the same event and announces its own full count (a cheaper count would jump).

const listAsks = vi.fn();
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
  getReadyToSend: async () => ({ total: 0 }),
}));
vi.mock("@/lib/assistant-state", () => ({ fetchSettingsShared: async () => ({ waiting: [] }) }));

const ask = (id: number) => ({ id, application_id: 100 + id, question: "Q?", context: "", job_title: "Dev", job_company: "Acme" });
const changed = () =>
  act(() => {
    window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { areas: ["asks"], full: false, seq: Math.random() } }));
  });

beforeEach(() => {
  listAsks.mockReset().mockResolvedValue({ asks: [ask(1)] });
});

describe("useOpenAsks live", () => {
  it("re-counts quietly when the assistant changes something", async () => {
    const { result } = renderHook(() => useOpenAsks(true, "/"));
    await waitFor(() => expect(result.current).toBe(1));
    listAsks.mockResolvedValue({ asks: [ask(1), ask(2)] });
    changed();
    await waitFor(() => expect(result.current).toBe(2));
  });

  it("does not re-count on Needs you (the page announces its own count)", async () => {
    renderHook(() => useOpenAsks(true, "/needs-you"));
    changed();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(listAsks).not.toHaveBeenCalled();
  });
});
