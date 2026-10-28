import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PauseUntil } from "./PauseUntil";
import { PausedBanner } from "@/components/layout/PausedBanner";
import { TopBar } from "@/components/layout/TopBar";
import { PAUSE_CHANGED_EVENT } from "@/lib/assistant-state";
import { isoToLocalInput, pauseUntilIso, pausedUntilText } from "@/lib/assistant-tab";

const getAssistantSettings = vi.fn();
const updateProfileFields = vi.fn();
const toastError = vi.fn();
const toastSuccess = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/connect" }));
vi.mock("@/components/layout/AuthProvider", () => ({ useAuth: () => ({ user: { email: "a@b.co" } }) }));
vi.mock("@/lib/api", () => ({
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
}));
vi.mock("sonner", () => ({
  toast: { error: (...a: unknown[]) => toastError(...a), success: (...a: unknown[]) => toastSuccess(...a) },
}));

const HOUR = 3_600_000;
const inHours = (h: number) => isoToLocalInput(new Date(Date.now() + h * HOUR).toISOString());
const Frame = () => (
  <>
    <TopBar />
    <PausedBanner />
    <PauseUntil />
  </>
);

beforeEach(() => {
  getAssistantSettings.mockReset().mockResolvedValue({ paused: false, waiting: [] });
  updateProfileFields.mockReset().mockResolvedValue({});
  toastError.mockReset();
  toastSuccess.mockReset();
});

describe("Pause until a time", () => {
  it("sends the time and the trimmed reason in one PATCH; top bar, banner and tab all show it", async () => {
    const fired = vi.fn();
    window.addEventListener(PAUSE_CHANGED_EVENT, fired);
    render(<Frame />);
    const local = inHours(48);
    fireEvent.change(screen.getByTestId("pause-until"), { target: { value: local } });
    fireEvent.change(screen.getByTestId("pause-reason"), { target: { value: "  On holiday  " } });
    fireEvent.click(screen.getByTestId("pause-until-save"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const iso = new Date(local).toISOString();
    expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "assistant_settings.paused_until", value: iso },
      { path: "assistant_settings.pause_reason", value: "On holiday" },
    ]);
    await waitFor(() => expect(fired).toHaveBeenCalled());
    expect((fired.mock.calls[0][0] as CustomEvent).detail).toBe(true);
    window.removeEventListener(PAUSE_CHANGED_EVENT, fired);
    expect(await screen.findByTestId("paused-until")).toHaveTextContent(pausedUntilText(iso));
    expect(screen.getByTestId("pause-button")).toHaveTextContent("Assistants paused");
    expect(screen.getByTestId("paused-banner")).toHaveTextContent(pausedUntilText(iso));
    // the reason is never echoed in a message
    for (const m of [...toastSuccess.mock.calls, ...toastError.mock.calls]) expect(String(m[0])).not.toContain("holiday");
  });

  it("a blank reason is not written at all", async () => {
    render(<Frame />);
    fireEvent.change(screen.getByTestId("pause-until"), { target: { value: inHours(2) } });
    fireEvent.change(screen.getByTestId("pause-reason"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("pause-until-save"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const sent = updateProfileFields.mock.calls[0][0] as { path: string }[];
    expect(sent.map((e) => e.path)).toEqual(["assistant_settings.paused_until"]);
  });

  it("a past time or one more than 365 days ahead is refused before any write", async () => {
    render(<Frame />);
    fireEvent.change(screen.getByTestId("pause-until"), { target: { value: inHours(-1) } });
    fireEvent.click(screen.getByTestId("pause-until-save"));
    fireEvent.change(screen.getByTestId("pause-until"), { target: { value: inHours(24 * 366) } });
    fireEvent.click(screen.getByTestId("pause-until-save"));
    expect(toastError).toHaveBeenCalledTimes(2);
    expect(toastError).toHaveBeenLastCalledWith("Pick a time in the future, at most 365 days from now. Not saved.");
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("a timed pause read from the server shows its end; an untimed one shows no time", async () => {
    const until = new Date(Date.now() + 5 * HOUR).toISOString();
    getAssistantSettings.mockResolvedValue({ paused: true, paused_until: { value: until, effective: until }, waiting: [] });
    render(<Frame />);
    expect(await screen.findByTestId("paused-until")).toHaveTextContent(pausedUntilText(until));
    expect(pausedUntilText("until_resumed")).toBe("");
    expect(pausedUntilText("")).toBe("");
  });

  it("the date rule: future and at most 365 days ahead", () => {
    const now = new Date("2026-10-28T12:00:00Z");
    expect("error" in pauseUntilIso("", now)).toBe(true);
    expect(pauseUntilIso(isoToLocalInput("2026-10-29T12:00:00Z"), now)).toEqual({ iso: "2026-10-29T12:00:00.000Z" });
    expect("error" in pauseUntilIso(isoToLocalInput("2027-10-30T12:00:00Z"), now)).toBe(true);
  });
});
