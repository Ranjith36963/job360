import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssistantTab } from "./AssistantTab";
import { pcOnText } from "@/lib/assistant-tab";

const getAssistantSettings = vi.fn();
const updateProfileFields = vi.fn();
vi.mock("@/lib/api", () => ({
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  getMorningCheck: vi.fn().mockResolvedValue({ state: { applied_today: 0, daily_cap: null } }),
  getProfileEditHistory: vi.fn().mockResolvedValue([]),
  getSettingHistory: vi.fn().mockResolvedValue([]),
  takeBackSetting: vi.fn(),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
}));
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: (m: string) => toastError(m) } }));

const field = (value: unknown, effective: unknown = value) => ({ value, effective });
const view = (pc: unknown = null) => ({
  apply_min_score: field(null, null),
  above_line: field("", "ask"),
  below_line: field("", "ask"),
  submit_mode: field("", "confirm"),
  daily_cap: field(null, null),
  pc_on_hours: field(pc, pc),
  inbox_mode: "ask",
  check_every: "",
});
const WORK = { days: ["mon", "tue"], from: "09:00", to: "18:00" };

beforeEach(() => {
  getAssistantSettings.mockReset().mockResolvedValue(view());
  updateProfileFields.mockReset().mockResolvedValue({});
  toastError.mockReset();
});

async function ready() {
  render(<AssistantTab />);
  await screen.findByTestId("pc-on-row");
}

describe("My PC-on time", () => {
  it("shows the row and its help, empty when nothing is set (any time)", async () => {
    await ready();
    expect(screen.getByTestId("assistant-tab")).toHaveTextContent("Your own browser");
    expect(screen.getByTestId("assistant-tab")).toHaveTextContent("My PC-on time");
    expect(screen.getByTestId("assistant-tab")).toHaveTextContent("Blank = any time.");
    expect((screen.getByTestId("pc-on-from") as HTMLInputElement).value).toBe("");
    expect(screen.queryByTestId("pc-on-clear")).toBeNull();
  });

  it("saves days and times as one PATCH value", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("pc-on-day-mon"));
    fireEvent.click(screen.getByTestId("pc-on-day-tue"));
    fireEvent.change(screen.getByTestId("pc-on-from"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByTestId("pc-on-to"), { target: { value: "18:00" } });
    fireEvent.click(screen.getByTestId("pc-on-save"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.pc_on_hours", value: WORK }]),
    );
  });

  it("refuses an incomplete or empty window without saving", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("pc-on-save"));
    expect(toastError).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("pc-on-day-mon"));
    fireEvent.change(screen.getByTestId("pc-on-from"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByTestId("pc-on-to"), { target: { value: "09:00" } });
    fireEvent.click(screen.getByTestId("pc-on-save"));
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("shows the saved window and clears it to null", async () => {
    getAssistantSettings.mockResolvedValue(view(WORK));
    await ready();
    await waitFor(() => expect((screen.getByTestId("pc-on-from") as HTMLInputElement).value).toBe("09:00"));
    expect(screen.getByTestId("pc-on-day-mon")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("pc-on-day-wed")).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByTestId("pc-on-clear"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([{ path: "assistant_settings.pc_on_hours", value: null }]),
    );
  });

  it("words a window for the history", () => {
    expect(pcOnText(WORK)).toBe("mon, tue 09:00 to 18:00");
    expect(pcOnText(null)).toBe("any time");
  });
});
