import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryTab } from "./MemoryTab";

const getProfile = vi.fn();
const getProfileEditHistory = vi.fn();
const updateProfileFields = vi.fn();
const keepProfileEdit = vi.fn();

vi.mock("@/lib/api", () => ({
  getProfile: (...a: unknown[]) => getProfile(...a),
  getProfileEditHistory: (...a: unknown[]) => getProfileEditHistory(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
  keepProfileEdit: (...a: unknown[]) => keepProfileEdit(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const contact = { email: "alex@example.com", phone: "+44 1" };
const profile = { user_info: { contact }, preferences: {} };
const agentHistory = [{ set_by: "agent:Claude", set_at: "2026-10-03T12:00:00Z", value: contact, source: "" }];
const webHistory = [{ set_by: "web", set_at: "2026-10-05T12:00:00Z", value: contact }];

function useHistory(rows: unknown[]) {
  getProfileEditHistory.mockImplementation(async (p: string) => (p === "user_info.contact" ? rows : []));
}

beforeEach(() => {
  getProfile.mockReset().mockResolvedValue(profile);
  getProfileEditHistory.mockReset();
  updateProfileFields.mockReset();
  keepProfileEdit.mockReset().mockResolvedValue(profile);
});

describe("MemoryTab keep all", () => {
  it("shows the count when two rows are unconfirmed", async () => {
    useHistory(agentHistory);
    render(<MemoryTab />);
    expect(await screen.findByTestId("memory-keep-all")).toHaveTextContent("Keep all 2");
  });

  it("is hidden when nothing is unconfirmed", async () => {
    useHistory(webHistory);
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    expect(screen.queryByTestId("memory-keep-all")).toBeNull();
  });

  it("keeps each unconfirmed block once, one at a time, then re-reads", async () => {
    useHistory(agentHistory);
    const order: string[] = [];
    let inFlight = 0;
    let maxInFlight = 0;
    keepProfileEdit.mockImplementation(async (path: string) => {
      inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
      await Promise.resolve();
      order.push(path);
      inFlight--;
      return profile;
    });
    render(<MemoryTab />);
    const btn = await screen.findByTestId("memory-keep-all");
    const readsBefore = getProfile.mock.calls.length;
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-busy", "true");
    expect(btn).toBeDisabled();
    // email and phone are two rows of ONE block (user_info.contact): one keep covers both
    await waitFor(() => expect(keepProfileEdit).toHaveBeenCalledTimes(1));
    expect(order).toEqual(["user_info.contact"]);
    expect(maxInFlight).toBe(1);
    // one re-read after the loop (confirm itself only re-reads history)
    await waitFor(() => expect(getProfile).toHaveBeenCalledTimes(readsBefore + 1));
    expect(screen.queryByTestId("memory-keep-all-error")).toBeNull();
  });

  it("stops at the first failure and shows an inline error", async () => {
    useHistory(agentHistory);
    keepProfileEdit.mockReset().mockRejectedValue(new Error("boom"));
    render(<MemoryTab />);
    fireEvent.click(await screen.findByTestId("memory-keep-all"));
    expect(await screen.findByTestId("memory-keep-all-error")).toBeInTheDocument();
    expect(keepProfileEdit).toHaveBeenCalledTimes(1);
  });
});
