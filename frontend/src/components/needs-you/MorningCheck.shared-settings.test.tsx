import { beforeEach, expect, it, vi } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import { MorningCheck } from "./MorningCheck";
import { fetchSettingsShared } from "@/lib/assistant-state";

// MorningCheck reads the assistant settings through the shared reader, so with the top bar's pause
// hook on the same page there is ONE GET /api/assistant-settings (prod trace 2026-11-13 showed two).

const api = vi.hoisted(() => ({ getAssistantSettings: vi.fn(), getMorningCheck: vi.fn(), updateProfileFields: vi.fn() }));
vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const EMPTY = { count: 0, items: [] };

function PauseLikeReader() {
  void fetchSettingsShared("/"); // what usePause does on mount
  return null;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  api.getAssistantSettings.mockResolvedValue({ auto_grant_count: { value: null }, auto_grant_until: { value: "" } });
  api.getMorningCheck.mockResolvedValue({ now: "2026-10-10T09:00:00+00:00", since: "2026-10-09T09:00:00+00:00", state: {},
    // The real shape: one bucket per kind (MorningCheck reads tally[kind].count for every one).
    tally: { sent: EMPTY, blocked: EMPTY, waiting: EMPTY, failed: EMPTY },
  });
});

it("MorningCheck and another shared settings reader make ONE assistant-settings call", async () => {
  render(
    <>
      <PauseLikeReader />
      <MorningCheck />
    </>,
  );
  await waitFor(() => expect(api.getMorningCheck).toHaveBeenCalled());
  await act(async () => {});
  expect(api.getAssistantSettings).toHaveBeenCalledTimes(1);
});
