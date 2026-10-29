/**
 * The Connect page's daily-check line must reflect what Job360 actually
 * stored — never a guess. While the profile is loading there is no line and
 * no reset button; if the read fails the page says so in neutral words and
 * never claims "your assistant will offer" (it does not know that).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ConnectAgentPage from "./ConnectPage";

// The page reads ?tab= from the URL (Setup | Schedules | Connected apps | Developers).
const nav = vi.hoisted(() => ({ tab: "schedules" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/connect",
  useSearchParams: () => new URLSearchParams(`tab=${nav.tab}`),
}));


const getProfile = vi.fn();
const updateProfileFields = vi.fn();
const listTokens = vi.fn();
// One active personal token = "connected" (see `connected` in page.tsx).
const connectedTokens = [
  { id: 1, name: "cli", prefix: "j360_ab", created_at: "2026-10-01T00:00:00Z" },
];

vi.mock("@/components/settings/CalendarFeedSection", () => ({ CalendarFeedSection: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: () => getProfile(),
  updateProfileFields: (...args: unknown[]) => updateProfileFields(...args),
  listTokens: () => listTokens(),
  listGrants: vi.fn().mockResolvedValue([]),
  getAssistantSettings: () => getAssistantSettings(),
  getMorningCheck: vi.fn().mockRejectedValue(new Error("not needed")),
  getSettingHistory: vi.fn().mockResolvedValue([]),
  getProfileEditHistory: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/components/layout/AuthProvider", () => ({ useAuth: () => ({ user: { email: "a@b.co" } }) }));

// The Permissions tab's Gmail check reads the assistant settings view.
const getAssistantSettings = vi.fn();
const unset = { value: null, effective: null };
const settings = (over: Record<string, unknown> = {}) => ({
  apply_min_score: unset, above_line: unset, below_line: unset, submit_mode: unset, daily_cap: unset,
  inbox_mode: "", check_every: "", waiting: [], paused: false, ...over,
});

describe("ConnectAgentPage — daily check status", () => {
  beforeEach(() => {
    nav.tab = "schedules";
    getAssistantSettings.mockReset().mockResolvedValue(settings());
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

  // One Gmail editor (owner 2026-10-28): the mode and how often moved to the Permissions tab.
  it("connected: mode control writes daily_check; legacy values display as Auto / Off", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    nav.tab = "permissions";
    getAssistantSettings.mockResolvedValueOnce(settings({ inbox_mode: "scheduled" }));
    render(<ConnectAgentPage />);
    // Wait for the saved mode, not just the button: it renders before the settings load.
    await waitFor(() => expect(screen.getByTestId("inbox-auto")).toHaveAttribute("aria-checked", "true"));
    // Every reload after the click reads "ask" (a second load would otherwise get the default and flake).
    getAssistantSettings.mockResolvedValue(settings({ inbox_mode: "ask" }));
    fireEvent.click(screen.getByTestId("inbox-ask"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.daily_check", value: "ask" }])
    );
    await waitFor(() => expect(screen.getByTestId("inbox-ask")).toHaveAttribute("aria-checked", "true"));
    fireEvent.click(screen.getByTestId("inbox-paused"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.daily_check", value: "paused" }])
    );
  });

  it("declined displays as Off and '' has no option selected but can be picked", async () => {
    nav.tab = "permissions";
    getAssistantSettings.mockResolvedValue(settings({ inbox_mode: "declined" }));
    const first = render(<ConnectAgentPage />);
    expect(await screen.findByTestId("inbox-paused")).toHaveAttribute("aria-checked", "true");
    first.unmount();

    getAssistantSettings.mockResolvedValue(settings());
    render(<ConnectAgentPage />);
    await screen.findByTestId("inbox-auto");
    for (const m of ["auto", "ask", "paused"]) {
      expect(screen.getByTestId(`inbox-${m}`)).toHaveAttribute("aria-checked", "false");
    }
    fireEvent.click(screen.getByTestId("inbox-auto"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.daily_check", value: "auto" }])
    );
  });

  it("frequency select shows - for '' (never a default) and writes check_every on change", async () => {
    nav.tab = "permissions";
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("check-every")) as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(select.selectedOptions[0].textContent).toBe("-");
    fireEvent.change(select, { target: { value: "3h" } });
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.check_every", value: "3h" }])
    );
  });

  it("frequency select shows a stored 1h as every 1 hour, not blank", async () => {
    nav.tab = "permissions";
    getAssistantSettings.mockResolvedValue(settings({ check_every: "1h" }));
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("check-every")) as HTMLSelectElement;
    expect(select.value).toBe("1h");
    expect(select.selectedOptions[0].textContent).toBe("1");
    expect(select.parentElement).toHaveTextContent(/hour$/);
  });

  it("frequency select reverts when the save fails", async () => {
    nav.tab = "permissions";
    getAssistantSettings.mockImplementation(async () => settings({ check_every: "12h" }));
    updateProfileFields.mockRejectedValue(new Error("nope"));
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("check-every")) as HTMLSelectElement;
    expect(select.value).toBe("12h");
    fireEvent.change(select, { target: { value: "6h" } });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalled());
    await waitFor(() => expect((screen.getByTestId("check-every") as HTMLSelectElement).value).toBe("12h"));
  });

  it("frequency select is locked while a save is in flight", async () => {
    nav.tab = "permissions";
    getAssistantSettings.mockResolvedValue(settings({ check_every: "12h" }));
    let finish: () => void = () => {};
    updateProfileFields.mockReturnValue(new Promise<void>((r) => (finish = r)));
    render(<ConnectAgentPage />);
    const select = (await screen.findByTestId("check-every")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "6h" } });
    await waitFor(() => expect(screen.getByTestId("check-every")).toBeDisabled());
    finish();
    await waitFor(() => expect(screen.getByTestId("check-every")).not.toBeDisabled());
  });

  it("the schedules tab has no mode control and no frequency select (they are on Permissions)", async () => {
    listTokens.mockResolvedValue(connectedTokens);
    getProfile.mockResolvedValue({ preferences: { daily_check: "auto" } });
    render(<ConnectAgentPage />);
    await screen.findByTestId("daily-check-status");
    for (const id of ["inbox-mode", "inbox-check-every", "inbox-auto", "check-every"]) {
      expect(screen.queryByTestId(id)).not.toBeInTheDocument();
    }
  });

  it("hides the controls when the settings read fails", async () => {
    nav.tab = "permissions";
    getAssistantSettings.mockRejectedValue(new Error("boom"));
    render(<ConnectAgentPage />);
    await screen.findByTestId("assistant-retry");
    expect(screen.queryByTestId("inbox-auto")).not.toBeInTheDocument();
    expect(screen.queryByTestId("check-every")).not.toBeInTheDocument();
  });
});
