import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";
import { SettingRequestCard, describeSettingRequest } from "./SettingRequestCard";

const confirmSettingRequest = vi.fn();
const declineSettingRequest = vi.fn();
const getAssistantSettings = vi.fn();
const listAsks = vi.fn();
const toastError = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
  answerAsk: vi.fn(),
  withdrawAsk: vi.fn(),
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  confirmSettingRequest: (...a: unknown[]) => confirmSettingRequest(...a),
  declineSettingRequest: (...a: unknown[]) => declineSettingRequest(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a) } }));

const evil = "<img src=x onerror=alert(1)>";
const request = {
  id: 11,
  path: "assistant_settings.apply_mode",
  value: "apply_all",
  requested_by: evil,
  requested_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  status: "waiting",
};

beforeEach(() => {
  confirmSettingRequest.mockReset().mockResolvedValue({});
  declineSettingRequest.mockReset().mockResolvedValue({});
  getAssistantSettings.mockReset().mockResolvedValue({ waiting: [request] });
  listAsks.mockReset().mockResolvedValue({ asks: [], open_count: 0 });
  toastError.mockReset();
});

describe("describeSettingRequest", () => {
  it("explains each gated path in plain words", () => {
    expect(describeSettingRequest("assistant_settings.apply_mode", "apply_all").change).toMatch(/every job/);
    expect(describeSettingRequest("assistant_settings.submit_mode", "auto_when_sure").change).toMatch(/on its own/);
    expect(describeSettingRequest("assistant_settings.apply_min_score", 60).change).toBe(
      "Only apply alone at 60 or above",
    );
    expect(describeSettingRequest("assistant_settings.daily_cap", null).change).toBe("No daily limit");
    expect(describeSettingRequest("assistant_settings.daily_cap", 5).change).toBe("Up to 5 applications a day");
    expect(describeSettingRequest("assistant_settings.paused_until", "").change).toBe("Start applying again");
    expect(describeSettingRequest("preferences.daily_check", "auto").title).toBe("Your inbox");
  });
});

describe("SettingRequestCard", () => {
  it("shows the change as literal text and says nothing happens until Confirm", () => {
    const { container } = render(<SettingRequestCard request={request} onChanged={async () => {}} />);
    expect(screen.getByTestId("setting-request-change")).toHaveTextContent(
      "Apply to every job I bring, without asking",
    );
    expect(screen.getByText(/Nothing changes until you confirm/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull(); // the actor name is text, never HTML
    expect(confirmSettingRequest).not.toHaveBeenCalled();
  });

  it("Confirm applies the request and reloads", async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(<SettingRequestCard request={request} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(confirmSettingRequest).toHaveBeenCalledWith(11));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(declineSettingRequest).not.toHaveBeenCalled();
  });

  it("Don't change declines it", async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(<SettingRequestCard request={request} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("setting-request-decline"));
    await waitFor(() => expect(declineSettingRequest).toHaveBeenCalledWith(11));
    expect(confirmSettingRequest).not.toHaveBeenCalled();
  });

  it("a failed confirm shows an error and keeps the card", async () => {
    confirmSettingRequest.mockRejectedValue(new Error("409"));
    const onChanged = vi.fn();
    render(<SettingRequestCard request={request} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByTestId("setting-request-11")).toBeInTheDocument();
  });
});

describe("Needs-you page", () => {
  it("lists the waiting requests and tells the badge asks + waiting", async () => {
    const seen: number[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("job360:asks-changed", on);
    listAsks.mockResolvedValue({ asks: [], open_count: 2 });
    render(<NeedsYou />);
    expect(await screen.findByTestId("setting-request-11")).toBeInTheDocument();
    expect(screen.getByText("Waiting for your OK")).toBeInTheDocument();
    await waitFor(() => expect(seen).toEqual([3])); // 2 open asks + 1 waiting
    // after the user confirms, the reload comes back empty and the badge follows
    getAssistantSettings.mockResolvedValue({ waiting: [] });
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(seen).toEqual([3, 2]));
    expect(screen.queryByTestId("setting-request-11")).toBeNull();
    window.removeEventListener("job360:asks-changed", on);
  });

  it("a failed settings read never hides the questions", async () => {
    getAssistantSettings.mockRejectedValue(new Error("down"));
    render(<NeedsYou />);
    expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
    expect(screen.queryByText("Waiting for your OK")).toBeNull();
  });
});
