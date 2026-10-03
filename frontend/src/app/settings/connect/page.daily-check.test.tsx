/**
 * The Connect page's daily-check line must reflect what Job360 actually
 * stored — never a guess. While the profile is loading there is no line and
 * no reset button; if the read fails the page says so in neutral words and
 * never claims "your assistant will offer" (it does not know that).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ConnectAgentPage from "./page";

const getProfile = vi.fn();
const updateProfileFields = vi.fn();
const listTokens = vi.fn();
// One active personal token = "connected" (see `connected` in page.tsx).
const connectedTokens = [
  { id: 1, name: "cli", prefix: "j360_ab", created_at: "2026-10-01T00:00:00Z" },
];

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: () => getProfile(),
  updateProfileFields: (...args: unknown[]) => updateProfileFields(...args),
  listTokens: () => listTokens(),
  listGrants: vi.fn().mockResolvedValue([]),
}));

describe("ConnectAgentPage — daily check status", () => {
  beforeEach(() => {
    getProfile.mockReset();
    updateProfileFields.mockReset();
    listTokens.mockReset();
    listTokens.mockResolvedValue([]);
    updateProfileFields.mockResolvedValue({});
  });

  it("shows no status line and no reset button while the profile is loading", async () => {
    getProfile.mockReturnValue(new Promise(() => {}));
    render(<ConnectAgentPage />);
    await waitFor(() => expect(getProfile).toHaveBeenCalled());
    expect(screen.queryByTestId("daily-check-status")).not.toBeInTheDocument();
    expect(screen.queryByTestId("daily-check-reset")).not.toBeInTheDocument();
  });

  it("shows a neutral line, never a 'will offer' guess, when the profile read fails", async () => {
    getProfile.mockRejectedValue(new Error("boom"));
    render(<ConnectAgentPage />);
    const status = await screen.findByTestId("daily-check-status");
    expect(status).toHaveTextContent(/couldn.t load this/i);
    // Scoped to the status line itself — Step 3's own copy ("your assistant
    // will offer to set up a daily check…") legitimately uses the same words
    // elsewhere on the page and is not the guess this test guards against.
    expect(status).not.toHaveTextContent(/will offer/i);
    expect(screen.queryByTestId("daily-check-reset")).not.toBeInTheDocument();
  });

  it("shows the declined line and the reset button once the read succeeds", async () => {
    getProfile.mockResolvedValue({ preferences: { daily_check: "declined" } });
    render(<ConnectAgentPage />);
    const status = await screen.findByTestId("daily-check-status");
    expect(status).toHaveTextContent(/you said no/i);
    expect(screen.getByTestId("daily-check-reset")).toBeInTheDocument();
  });

  it("connected: mode control writes daily_check; legacy values display as Auto / Off", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockResolvedValue({ preferences: { daily_check: "scheduled" } });
    render(<ConnectAgentPage />);
    const group = await screen.findByTestId("inbox-mode");
    expect(group).toBeInTheDocument();
    expect(screen.getByTestId("inbox-mode-auto")).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByTestId("inbox-check-toggle")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("inbox-mode-ask"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "preferences.daily_check", value: "ask" },
      ])
    );
    await waitFor(() =>
      expect(screen.getByTestId("inbox-mode-ask")).toHaveAttribute("aria-checked", "true")
    );
    fireEvent.click(screen.getByTestId("inbox-mode-paused"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "preferences.daily_check", value: "paused" },
      ])
    );
    expect(screen.getByTestId("daily-check-status")).toHaveTextContent(/off/i);
  });

  it("declined displays as Off and '' has no option selected but can be picked", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockResolvedValue({ preferences: { daily_check: "declined" } });
    const first = render(<ConnectAgentPage />);
    await screen.findByTestId("inbox-mode");
    expect(screen.getByTestId("inbox-mode-paused")).toHaveAttribute("aria-checked", "true");
    first.unmount();

    getProfile.mockResolvedValue({ preferences: {} });
    render(<ConnectAgentPage />);
    await screen.findByTestId("inbox-mode");
    for (const m of ["auto", "ask", "paused"]) {
      expect(screen.getByTestId(`inbox-mode-${m}`)).toHaveAttribute("aria-checked", "false");
    }
    fireEvent.click(screen.getByTestId("inbox-mode-auto"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "preferences.daily_check", value: "auto" },
      ])
    );
  });

  it("frequency select shows Once a day for '' and writes check_every on change", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockResolvedValue({ preferences: { daily_check: "auto" } });
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("inbox-check-every")) as HTMLSelectElement;
    expect(select.value).toBe("24h");
    fireEvent.change(select, { target: { value: "3h" } });
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "preferences.check_every", value: "3h" },
      ])
    );
    expect((screen.getByTestId("inbox-check-every") as HTMLSelectElement).value).toBe("3h");
  });

  it("frequency select reverts when the save fails", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockResolvedValue({ preferences: { check_every: "12h" } });
    updateProfileFields.mockRejectedValue(new Error("nope"));
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("inbox-check-every")) as HTMLSelectElement;
    expect(select.value).toBe("12h");
    fireEvent.change(select, { target: { value: "6h" } });
    await waitFor(() =>
      expect((screen.getByTestId("inbox-check-every") as HTMLSelectElement).value).toBe("12h")
    );
  });

  it("frequency select is locked while a save is in flight", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockResolvedValue({ preferences: { check_every: "12h" } });
    let finish: () => void = () => {};
    updateProfileFields.mockReturnValue(new Promise<void>((r) => (finish = r)));
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("inbox-check-every")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "6h" } });
    await waitFor(() => expect(screen.getByTestId("inbox-check-every")).toBeDisabled());
    finish();
    await waitFor(() => expect(screen.getByTestId("inbox-check-every")).not.toBeDisabled());
  });

  it("not connected: no mode control and no frequency select", async () => {
    getProfile.mockResolvedValue({ preferences: { daily_check: "auto" } });
    render(<ConnectAgentPage />);
    await screen.findByTestId("daily-check-status");
    await waitFor(() => expect(getProfile).toHaveBeenCalled());
    expect(screen.queryByTestId("inbox-mode")).not.toBeInTheDocument();
    expect(screen.queryByTestId("inbox-check-every")).not.toBeInTheDocument();
  });

  it("hides the controls when the profile read fails", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockRejectedValue(new Error("boom"));
    render(<ConnectAgentPage />);
    await screen.findByTestId("daily-check-status");
    expect(screen.queryByTestId("inbox-mode")).not.toBeInTheDocument();
    expect(screen.queryByTestId("inbox-check-every")).not.toBeInTheDocument();
  });
});
