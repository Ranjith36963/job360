import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";
import type { JobCardData } from "@/lib/job-cards";
import { ApiError } from "@/lib/api-error";

let cards: Partial<JobCardData>[] = [];
const requestJson = vi.fn();
vi.mock("@/lib/api", () => ({
  requestJson: (...a: unknown[]) => requestJson(...a),
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: async () => ({ asks: [], open_count: 0 }),
  answerAsk: vi.fn(), withdrawAsk: vi.fn(), confirmAsk: vi.fn(),
  confirmSettingRequest: vi.fn(), declineSettingRequest: vi.fn(),
}));
vi.mock("./JobCards", async () => {
  const { useEffect } = await import("react");
  return {
    JobCards: ({ onReport }: { onReport: (r: unknown) => void }) => {
      useEffect(() => onReport({ cards, failed: [], total: cards.length, settled: true }), [onReport]);
      return <ul data-testid="job-cards" />;
    },
  };
});
vi.mock("./OutcomeSuggestions", () => ({ OutcomeSuggestions: () => null }));
vi.mock("./UnmatchedMail", () => ({ UnmatchedMail: () => null }));
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: vi.fn() } }));

beforeEach(() => {
  toastSuccess.mockReset();
  requestJson.mockReset().mockImplementation(async (path: string) => {
    if (path === "/api/own-browser/queue") throw new Error("no queue");
    return {};
  });
});

describe("Run my own-browser tasks now", () => {
  it("is absent when no card needs the owner's Chrome", async () => {
    cards = [{ applicationId: 1, ownBrowser: false, ownBrowserCount: 0 }];
    render(<NeedsYou />);
    await screen.findByTestId("job-cards");
    expect(screen.queryByTestId("run-chrome-tasks")).toBeNull();
  });

  it("POSTs run-now, toasts, and drops the copy-a-command step on success", async () => {
    cards = [{ applicationId: 1, ownBrowser: true, ownBrowserCount: 1 }];
    render(<NeedsYou />);
    fireEvent.click(await screen.findByTestId("run-chrome-tasks"));
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith("/api/own-browser/run-now", "POST"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Your own-browser tasks are starting"));
    expect(await screen.findByText("Keep your PC on and your assistant open in your own browser.")).toBeInTheDocument();
    expect(screen.getByText(/It works through the jobs marked Needs your own browser: turn your PC on\./)).toBeInTheDocument();
    expect(screen.queryByTestId("run-chrome-command")).toBeNull();
  });

  it.each([404, 405])("falls back to the copy-a-command panel on %i", async (status) => {
    cards = [{ applicationId: 1, ownBrowser: true, ownBrowserCount: 1 }];
    requestJson.mockImplementation(async (path: string) => {
      if (path === "/api/own-browser/run-now") throw new ApiError(status, "nope");
      throw new Error("no queue");
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<NeedsYou />);
    fireEvent.click(await screen.findByTestId("run-chrome-tasks"));
    expect(await screen.findByText("Turn on your PC, open your assistant in your own browser and say:")).toBeInTheDocument();
    expect(screen.getByTestId("run-chrome-command")).toHaveTextContent("run 360 batch");
    expect(toastSuccess).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("run-chrome-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("run 360 batch"));
  });

  it("shows the queue count when the queue answers, in either shape", async () => {
    cards = [{ applicationId: 1, ownBrowser: true, ownBrowserCount: 1 }];
    requestJson.mockImplementation(async () => ({ items: [1, 2, 3] }));
    render(<NeedsYou />);
    expect(await screen.findByTestId("run-chrome-queue")).toHaveTextContent("3 waiting for your browser");
  });

  it("hides the queue count when the queue fails", async () => {
    cards = [{ applicationId: 1, ownBrowser: true, ownBrowserCount: 1 }];
    render(<NeedsYou />);
    await screen.findByTestId("run-chrome-tasks");
    expect(screen.queryByTestId("run-chrome-queue")).toBeNull();
  });
});
