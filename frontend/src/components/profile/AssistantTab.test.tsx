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
  apply_min_score: field(null, null),
  above_line: field("", "ask"),
  below_line: field("", "ask"),
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
      "Your score line", "Score line", "Batch auto apply: off", "Batch auto apply: on",
      "Your assistant asks you before each application.",
      "Your assistant applies on its own to every job at or above your score line.",
      "Your assistant always stops and asks you when it is not sure: a CAPTCHA, a login page, a guessed answer, or anything not verified.",
      "Limits & inbox", "Daily limit", "Most applications per day. Leave blank for no limit.",
      "Gmail check", "Auto", "Ask first", "Off", "Hard stops", "Always on. Cannot be switched off.",
      "History", "Every change, newest first",
    ]) {
      expect(tab).toHaveTextContent(t);
    }
    expect(screen.getByTestId("batch-off")).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByTestId("above-line-ask")).toBeNull();
    expect(screen.queryByTestId("submit-mode-confirm")).toBeNull();
    // an older "ask" below the line is shown honestly, with what a click will do
    expect(screen.getByTestId("below-line-rule")).toHaveTextContent(
      "Below the line: asks you. Choosing either option above drops them from now on.",
    );
    expect(tab).not.toHaveTextContent("Submit mode");
    expect(screen.getByTestId("inbox-auto")).toHaveAttribute("aria-checked", "true");
    expect((screen.getByTestId("check-every") as HTMLSelectElement).value).toBe("6h");
    // No default line: unset shows the plain "not set" words, and the input is empty.
    expect(screen.getByTestId("score-line-unset")).toHaveTextContent("Not set — your assistant will ask you");
    expect((screen.getByTestId("score-input") as HTMLInputElement).value).toBe("");
    expect(tab.textContent).not.toMatch(/\b75\b/);
  });

  it("each control sends its own PATCH body as the web user, then re-reads", async () => {
    await ready();
    // Batch auto apply ON: both halves in one PATCH, and below-the-line jobs are dropped.
    fireEvent.click(screen.getByTestId("batch-on"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "assistant_settings.above_line", value: "auto" },
      { path: "assistant_settings.submit_mode", value: "auto_when_sure" },
      { path: "assistant_settings.below_line", value: "skip" },
    ]));
    fireEvent.click(screen.getByTestId("inbox-ask"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.daily_check", value: "ask" }]));
    fireEvent.change(screen.getByTestId("check-every"), { target: { value: "12h" } });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.check_every", value: "12h" }]));
    const cap = screen.getByTestId("daily-cap");
    fireEvent.change(cap, { target: { value: "10" } });
    fireEvent.blur(cap);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.daily_cap", value: 10 }]));
    expect(toastSuccess).toHaveBeenCalledWith("Saved.");
    // one first read + one re-read per save (batch, Gmail, every, daily limit)
    expect(getAssistantSettings.mock.calls.length).toBeGreaterThanOrEqual(5);
  });

  it("automatic employer emails: turning on asks to confirm first, turning off applies at once", async () => {
    await ready();
    const toggle = screen.getByTestId("auto-employer-emails-toggle");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("assistant-tab")).toHaveTextContent(
      "Check-in 7 days after applying, a feedback request after a rejection, one follow-up 7 days later. Sent in your name by your assistant.",
    );
    fireEvent.click(toggle);
    expect(updateProfileFields).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("auto-employer-emails-confirm"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.auto_employer_emails", value: true }]),
    );
  });

  it("automatic employer emails: Cancel sends nothing; off is one click", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("auto-employer-emails-toggle"));
    fireEvent.click(screen.getByTestId("auto-employer-emails-cancel"));
    expect(updateProfileFields).not.toHaveBeenCalled();
    getAssistantSettings.mockResolvedValue(view({ auto_employer_emails: field(true, true) }));
    await ready();
    const on = screen.getAllByTestId("auto-employer-emails-toggle").find((t) => t.getAttribute("aria-checked") === "true");
    expect(on).toBeTruthy();
    fireEvent.click(on!);
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.auto_employer_emails", value: false }]),
    );
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

  it("the score line saves 0-100 and rejects bad numbers", async () => {
    getAssistantSettings.mockResolvedValue(view({ apply_min_score: field(70) }));
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

  it("clearing a saved score line sends null (back to not set)", async () => {
    getAssistantSettings.mockResolvedValue(view({ apply_min_score: field(70) }));
    await ready();
    expect(screen.queryByTestId("score-line-unset")).toBeNull();
    const n = screen.getByTestId("score-input") as HTMLInputElement;
    fireEvent.change(n, { target: { value: "" } });
    fireEvent.blur(n);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.apply_min_score", value: null }]));
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
    getAssistantSettings.mockResolvedValue(view({ apply_min_score: field(70) }));
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
    fireEvent.click(screen.getByTestId("batch-on"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Could not save. Try again."));
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.getByTestId("batch-off")).toHaveAttribute("aria-checked", "true");
  });

  it("batch reads on only when both halves are on; OFF writes ask + confirm; a dropped line says so", async () => {
    getAssistantSettings.mockResolvedValue(view({
      above_line: field("auto"), submit_mode: field("auto_when_sure"), below_line: field("skip"),
    }));
    await ready();
    expect(screen.getByTestId("batch-on")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("below-line-rule")).toHaveTextContent("Jobs below your score line are dropped — never applied to.");
    fireEvent.click(screen.getByTestId("batch-off"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "assistant_settings.above_line", value: "ask" },
      { path: "assistant_settings.submit_mode", value: "confirm" },
    ]));
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
    for (const t of ["Right now", "Score line", "Batch auto applyOff", "Below the lineAsk me", "Today4 of 10", "Take back adds a new row; nothing is erased."]) {
      expect(rail).toHaveTextContent(t);
    }
  });

  it("a failed read says so and offers Try again", async () => {
    getAssistantSettings.mockRejectedValueOnce(new Error("down"));
    render(<AssistantTab />);
    fireEvent.click(await screen.findByTestId("assistant-retry"));
    await screen.findByTestId("assistant-tab");
  });

  it("pace limits save as numbers; No limit and 0 both mean no limit (never a default)", async () => {
    getAssistantSettings.mockResolvedValue(view({ company_cap: field(2, 2), weekly_cap: field(null, null) }));
    await ready();
    expect(screen.getByTestId("company-cap-none")).toHaveAttribute("aria-checked", "false");
    expect((screen.getByTestId("company-cap") as HTMLInputElement).value).toBe("2");
    expect((screen.getByTestId("company-cap-days") as HTMLInputElement).value).toBe("");
    const days = screen.getByTestId("company-cap-days");
    fireEvent.change(days, { target: { value: "0" } });
    fireEvent.blur(days);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.company_cap_days", value: 0 }]));
    const cap = screen.getByTestId("company-cap");
    fireEvent.change(cap, { target: { value: "0" } });
    fireEvent.blur(cap);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.company_cap", value: null }]));
    // weekly: no limit is ticked and the box is hidden until the tick is taken off
    expect(screen.getByTestId("weekly-cap-none")).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByTestId("weekly-cap")).toBeNull();
    fireEvent.click(screen.getByTestId("weekly-cap-none"));
    const weekly = screen.getByTestId("weekly-cap");
    fireEvent.change(weekly, { target: { value: "20" } });
    fireEvent.blur(weekly);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.weekly_cap", value: 20 }]));
    // minutes and seconds
    fireEvent.change(screen.getByTestId("min-gap"), { target: { value: "1" } });
    const sec = screen.getByTestId("min-gap-seconds");
    fireEvent.change(sec, { target: { value: "30" } });
    fireEvent.blur(sec);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.min_gap_minutes", value: 1.5 }]));
  });

  it("ticking No limit clears a saved limit", async () => {
    getAssistantSettings.mockResolvedValue(view({ weekly_cap: field(30, 30) }));
    await ready();
    fireEvent.click(screen.getByTestId("weekly-cap-none"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.weekly_cap", value: null }]));
  });

  it("a bad pace number says so and is not sent", async () => {
    await ready();
    const sec = screen.getByTestId("min-gap-seconds");
    fireEvent.change(sec, { target: { value: "75" } });
    fireEvent.blur(sec);
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalled();
  });

  it("the modes: one of three, each turns the other two off", async () => {
    await ready();
    expect(screen.getByTestId("mode-ask")).toHaveAttribute("aria-checked", "true");
    // Straight away asks once, then sends on its own with no hours and no grant
    fireEvent.click(screen.getByTestId("mode-straight"));
    expect(updateProfileFields).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("mode-straight-confirm"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "assistant_settings.submit_mode", value: "auto_when_sure" },
        { path: "assistant_settings.apply_hours", value: null },
        { path: "assistant_settings.auto_grant_count", value: null },
        { path: "assistant_settings.auto_grant_until", value: null },
      ]),
    );
  });

  it("Hours to apply saves the window and sending on its own together, only once both times are set", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("mode-hours"));
    const from = screen.getByTestId("apply-hours-from");
    const to = screen.getByTestId("apply-hours-to");
    // The real order: pick "from", focus leaves it, then pick "to" (CodeRabbit 2026-10-18).
    fireEvent.change(from, { target: { value: "09:00" } });
    fireEvent.blur(from);
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    expect((from as HTMLInputElement).value).toBe("09:00");
    fireEvent.change(to, { target: { value: "09:00" } });
    fireEvent.blur(to);
    expect(updateProfileFields).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith("Pick two different times, or clear both. Not saved.");
    fireEvent.change(from, { target: { value: "09:00" } });
    fireEvent.blur(from);
    fireEvent.change(to, { target: { value: "17:30" } });
    fireEvent.blur(to);
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "assistant_settings.apply_hours", value: { from: "09:00", to: "17:30" } },
        { path: "assistant_settings.submit_mode", value: "auto_when_sure" },
        { path: "assistant_settings.auto_grant_count", value: null },
        { path: "assistant_settings.auto_grant_until", value: null },
      ]),
    );
  });

  it("Ask me each time turns sending on its own off", async () => {
    getAssistantSettings.mockResolvedValue(view({ submit_mode: field("auto_when_sure", "auto_when_sure") }));
    await ready();
    expect(screen.getByTestId("mode-straight")).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByTestId("mode-ask"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith(
        expect.arrayContaining([{ path: "assistant_settings.submit_mode", value: "confirm" }]),
      ),
    );
  });

  it("auto for my next N applications: asks to confirm, then saves count and time together", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("mode-while"));
    fireEvent.change(screen.getByTestId("auto-grant-count"), { target: { value: "5" } });
    fireEvent.change(screen.getByTestId("auto-grant-until"), { target: { value: "2099-01-02T18:00" } });
    fireEvent.click(screen.getByTestId("auto-grant-save"));
    expect(updateProfileFields).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("auto-grant-confirm"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalled());
    const sent = updateProfileFields.mock.calls[0][0] as { path: string; value: unknown }[];
    expect(sent[0]).toEqual({ path: "assistant_settings.auto_grant_count", value: 5 });
    expect(sent[1].path).toBe("assistant_settings.auto_grant_until");
    expect(new Date(String(sent[1].value)).getFullYear()).toBe(2099);
    expect(sent.slice(2)).toEqual([
      { path: "assistant_settings.submit_mode", value: "confirm" },
      { path: "assistant_settings.apply_hours", value: null },
    ]);
  });

  it("a running grant is shown in words and can be stopped", async () => {
    const until = new Date(Date.now() + 3_600_000).toISOString();
    getAssistantSettings.mockResolvedValue(view({ auto_grant_count: field(5, 5), auto_grant_until: field(until, until) }));
    await ready();
    expect(screen.getByTestId("assistant-tab")).toHaveTextContent("Auto for my next 5 applications until");
    fireEvent.click(screen.getByTestId("auto-grant-stop"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "assistant_settings.auto_grant_count", value: null },
        { path: "assistant_settings.auto_grant_until", value: null },
      ]),
    );
  });
});
