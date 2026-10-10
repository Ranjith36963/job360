import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AssistantTab } from "./AssistantTab";
import { historyWhen } from "@/lib/assistant-tab";

const getAssistantSettings = vi.fn();
const getMorningCheck = vi.fn();
const getProfileEditHistory = vi.fn();
const getSettingHistory = vi.fn();
const takeBackSetting = vi.fn();
const updateProfileFields = vi.fn();
vi.mock("@/lib/api", () => ({
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  getMorningCheck: (...a: unknown[]) => getMorningCheck(...a),
  getProfileEditHistory: (...a: unknown[]) => getProfileEditHistory(...a),
  getSettingHistory: (...a: unknown[]) => getSettingHistory(...a),
  takeBackSetting: (...a: unknown[]) => takeBackSetting(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
}));
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }));

const field = (value: unknown, effective: unknown = value) => ({ value, effective });
const view = (over: Record<string, unknown> = {}) => ({
  apply_mode: field("", "ask_each"),
  apply_min_score: field(null, 75),
  submit_mode: field("", "confirm"),
  daily_cap: field(null, null),
  inbox_mode: "auto",
  check_every: "6h",
  ...over,
});
const check = { state: { applied_today: 4, daily_cap: 10 } };

const NOW = new Date().toISOString();
const histories: Record<string, unknown[]> = {
  "assistant_settings.daily_cap": [
    { set_by: "web", set_at: NOW, value: 10 },
    { set_by: "agent:Claude", set_at: "2026-10-01T08:00:00Z", value: 5 },
  ],
  "preferences.check_every": [{ set_by: "agent:Claude Code", set_at: "2026-10-02T21:02:00Z", value: "6h" }],
};

beforeEach(() => {
  getAssistantSettings.mockReset().mockResolvedValue(view());
  getMorningCheck.mockReset().mockResolvedValue(check);
  getSettingHistory.mockReset().mockImplementation(async (p: string) => histories[p] ?? []);
  getProfileEditHistory.mockReset().mockImplementation(async (p: string) => histories[p] ?? []);
  takeBackSetting.mockReset().mockResolvedValue(view());
  updateProfileFields.mockReset().mockResolvedValue({});
  toastSuccess.mockReset();
  toastError.mockReset();
});

async function ready() {
  render(<AssistantTab />);
  await screen.findByTestId("assistant-tab");
  await screen.findAllByTestId("history-row");
}

describe("AssistantTab", () => {
  it("shows the exact labels and descriptions, with the saved choice selected", async () => {
    await ready();
    const tab = screen.getByTestId("assistant-tab");
    for (const t of [
      "Apply mode", "When may your assistant apply?",
      "Ask about each job", "Apply to all", "Only above my score line",
      "Before any work, it shows you the fit and asks: apply or skip?",
      "Submit mode", "The last click on the form", "Confirm before sending", "Send on its own when sure",
      "It stops at Submit and shows you Ready to send.",
      "Limits & inbox", "Daily limit", "Most applications per day. Leave blank for no limit.",
      "Gmail check", "Auto", "Ask first", "Off", "Hard stops", "Always on. Cannot be switched off.",
      "History", "Every change, newest first",
    ]) {
      expect(tab).toHaveTextContent(t);
    }
    expect(screen.getByTestId("apply-mode-ask_each")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("submit-mode-confirm")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("inbox-auto")).toHaveAttribute("aria-checked", "true");
    expect((screen.getByTestId("check-every") as HTMLSelectElement).value).toBe("6h");
    expect(screen.queryByTestId("score-input")).toBeNull();
  });

  it("each control sends its own PATCH body as the web user, then re-reads", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("apply-mode-apply_all"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.apply_mode", value: "apply_all" }]));
    fireEvent.click(screen.getByTestId("submit-mode-auto_when_sure"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.submit_mode", value: "auto_when_sure" }]));
    fireEvent.click(screen.getByTestId("inbox-ask"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.daily_check", value: "ask" }]));
    fireEvent.change(screen.getByTestId("check-every"), { target: { value: "12h" } });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.check_every", value: "12h" }]));
    const cap = screen.getByTestId("daily-cap");
    fireEvent.change(cap, { target: { value: "10" } });
    fireEvent.blur(cap);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.daily_cap", value: 10 }]));
    expect(toastSuccess).toHaveBeenCalledWith("Saved.");
    expect(getAssistantSettings.mock.calls.length).toBeGreaterThan(5);
  });

  it("a blank daily limit clears it (no limit)", async () => {
    getAssistantSettings.mockResolvedValue(view({ daily_cap: field(10, 10) }));
    await ready();
    const cap = screen.getByTestId("daily-cap") as HTMLInputElement;
    expect(cap.value).toBe("10");
    fireEvent.change(cap, { target: { value: "" } });
    fireEvent.blur(cap);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.daily_cap", value: null }]));
  });

  it("the score line shows only for 'Only above my score line' and saves 0-100", async () => {
    getAssistantSettings.mockResolvedValue(view({ apply_mode: field("selective_above_score"), apply_min_score: field(70) }));
    await ready();
    const n = screen.getByTestId("score-input") as HTMLInputElement;
    expect(n.value).toBe("70");
    fireEvent.change(n, { target: { value: "75" } });
    fireEvent.blur(n);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.apply_min_score", value: 75 }]));
    updateProfileFields.mockClear();
    fireEvent.change(n, { target: { value: "150" } });
    fireEvent.blur(n);
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith("Score line must be a whole number from 0 to 100. Not saved.");
    expect(n.value).toBe("70");
  });

  it("an out-of-range daily limit says so and is not sent", async () => {
    await ready();
    const cap = screen.getByTestId("daily-cap") as HTMLInputElement;
    fireEvent.change(cap, { target: { value: "501" } });
    fireEvent.blur(cap);
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith("Daily limit must be a whole number from 1 to 500, or blank for no limit. Not saved.");
    expect(cap.value).toBe("");
  });

  it("Enter commits the daily limit like blur (save, and error on bad value)", async () => {
    await ready();
    const cap = screen.getByTestId("daily-cap") as HTMLInputElement;
    fireEvent.change(cap, { target: { value: "501" } });
    fireEvent.keyDown(cap, { key: "Enter" });
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith("Daily limit must be a whole number from 1 to 500, or blank for no limit. Not saved.");
    fireEvent.change(cap, { target: { value: "12" } });
    fireEvent.keyDown(cap, { key: "Enter" });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.daily_cap", value: 12 }]));
    expect(toastSuccess).toHaveBeenCalledWith("Saved.");
    expect(updateProfileFields).toHaveBeenCalledTimes(1);
  });

  it("Enter commits the score line like blur", async () => {
    getAssistantSettings.mockResolvedValue(view({ apply_mode: field("selective_above_score"), apply_min_score: field(70) }));
    await ready();
    const n = screen.getByTestId("score-input") as HTMLInputElement;
    fireEvent.change(n, { target: { value: "150" } });
    fireEvent.keyDown(n, { key: "Enter" });
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith("Score line must be a whole number from 0 to 100. Not saved.");
    expect(n.value).toBe("70");
    fireEvent.change(n, { target: { value: "80" } });
    fireEvent.keyDown(n, { key: "Enter" });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.apply_min_score", value: 80 }]));
    expect(toastSuccess).toHaveBeenCalledWith("Saved.");
  });

  it("a refused save shows the error and never 'Saved.'", async () => {
    updateProfileFields.mockRejectedValueOnce(new Error("422"));
    await ready();
    fireEvent.click(screen.getByTestId("apply-mode-apply_all"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Could not save. Try again."));
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.getByTestId("apply-mode-ask_each")).toHaveAttribute("aria-checked", "true");
  });

  it("hard stops are read-only text with no control", async () => {
    await ready();
    const stops = screen.getAllByTestId("hard-stop");
    expect(stops).toHaveLength(3);
    expect(stops[0]).toHaveTextContent("ALWAYS");
    expect(stops[0]).toHaveTextContent("Indeed and LinkedIn: your assistant always asks you first.");
    expect(stops[1]).toHaveTextContent("A login page or a CAPTCHA: your assistant pauses and asks you.");
    for (const s of stops) expect(within(s).queryByRole("button")).toBeNull();
    expect(stops[2]).toHaveTextContent("a practice run, it stops for you to check.");
  });

  it("history: who, when, was -> now; Take back only on the newest change per setting", async () => {
    await ready();
    const rows = screen.getAllByTestId("history-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(`${historyWhen(NOW)}`);
    expect(rows[0]).toHaveTextContent("You Daily limit: 5 → 10");
    expect(rows[1]).toHaveTextContent("Claude Code Inbox check: not set → every 6 hours");
    expect(rows[2]).toHaveTextContent("Claude Daily limit: no limit → 5");
    expect(screen.getAllByTestId("history-take-back")).toHaveLength(2);
  });

  it("Take back: a setting uses the take-back route, a preference PATCHes the previous value as web", async () => {
    await ready();
    const rows = screen.getAllByTestId("history-row");
    fireEvent.click(within(rows[0]).getByTestId("history-take-back"));
    await waitFor(() => expect(takeBackSetting).toHaveBeenCalledWith("assistant_settings.daily_cap"));
    expect(updateProfileFields).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getAllByTestId("history-row")[1]).getByTestId("history-take-back"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.check_every", value: null }]));
  });

  it("right rail: Right now, today's count, and the note", async () => {
    await ready();
    const rail = screen.getByTestId("assistant-rail");
    for (const t of ["Right now", "Ask about each job", "Confirm before sending", "Today4 of 10", "Take back adds a new row; nothing is erased."]) {
      expect(rail).toHaveTextContent(t);
    }
  });

  it("a failed read says so and offers Try again", async () => {
    getAssistantSettings.mockRejectedValueOnce(new Error("down"));
    render(<AssistantTab />);
    fireEvent.click(await screen.findByTestId("assistant-retry"));
    await screen.findByTestId("assistant-tab");
  });
});
