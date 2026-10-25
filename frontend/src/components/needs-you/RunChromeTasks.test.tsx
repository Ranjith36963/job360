import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";
import type { JobCardData } from "@/lib/job-cards";

let cards: Partial<JobCardData>[] = [];
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: async () => ({ asks: [], open_count: 0 }),
  answerAsk: vi.fn(), withdrawAsk: vi.fn(), confirmAsk: vi.fn(),
  confirmSettingRequest: vi.fn(), declineSettingRequest: vi.fn(),
}));
vi.mock("./JobCards", () => ({
  loadJobCards: async () => ({ cards, general: [] }),
  JobCards: () => <ul data-testid="job-cards" />,
}));
vi.mock("./BatchApplyPanel", () => ({ BatchApplyPanel: () => null }));
vi.mock("./MorningCheck", () => ({ MorningCheck: () => null }));
vi.mock("./OutcomeSuggestions", () => ({ OutcomeSuggestions: () => null }));
vi.mock("./CvToApprove", () => ({ CvToApprove: () => null }));
vi.mock("./UnmatchedMail", () => ({ UnmatchedMail: () => null }));
vi.mock("./ReadyToSend", () => ({ ReadyToSend: () => null }));
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: vi.fn() } }));

beforeEach(() => {
  toastSuccess.mockReset();
});

describe("Run my Chrome tasks now", () => {
  it("is absent when no card needs the owner's Chrome", async () => {
    cards = [{ applicationId: 1, ownBrowser: false, ownBrowserCount: 0 }];
    render(<NeedsYou />);
    await screen.findByTestId("job-cards");
    expect(screen.queryByTestId("run-chrome-tasks")).toBeNull();
  });

  it("opens an inline panel with the command, and a Copy button; no PC-on time", async () => {
    cards = [{ applicationId: 1, ownBrowser: true, ownBrowserCount: 1 }];
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<NeedsYou />);
    fireEvent.click(await screen.findByTestId("run-chrome-tasks"));
    expect(screen.getByText("Turn on your PC, open your assistant in Chrome and say:")).toBeInTheDocument();
    expect(screen.getByTestId("run-chrome-command")).toHaveTextContent("run 360 batch");
    expect(screen.getByText(/It works through the jobs marked Needs your Chrome, PC on\./)).toBeInTheDocument();
    expect(screen.queryByTestId("run-chrome-pc-on")).toBeNull();
    fireEvent.click(screen.getByTestId("run-chrome-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("run 360 batch"));
  });
});
