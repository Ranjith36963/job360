import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RunNowPanel } from "./RunNowPanel";
import { windowText } from "@/lib/batch-queue";
import { ApiError } from "@/lib/api-error";

const getDutyTriggers = vi.fn();
const runDuty = vi.fn();
vi.mock("@/lib/api", () => ({
  getDutyTriggers: () => getDutyTriggers(),
  runDuty: (...a: unknown[]) => runDuty(...a),
}));
const getOwnBrowserQueue = vi.fn();
const runOwnBrowserNow = vi.fn();
vi.mock("@/lib/browser-contract", () => ({
  getOwnBrowserQueue: () => getOwnBrowserQueue(),
  runOwnBrowserNow: () => runOwnBrowserNow(),
}));
const toastSuccess = vi.fn();
const toastInfo = vi.fn();
const toastApiError = vi.fn();
vi.mock("@/lib/toast", () => ({
  toast: {
    success: (m: string) => toastSuccess(m),
    info: (m: string) => toastInfo(m),
    apiError: (e: unknown, m: string) => toastApiError(e, m),
  },
}));

const BAD = /chrome|claude/i;
const queue = (over: Record<string, unknown> = {}) => ({
  count: 2, open_now: false, opens_at: null, why: "any_time", tasks: [], pc_on_hours: null, run_now_until: null, ...over,
});
const duty = (d: string, label: string, set = true) => ({ duty: d, label, set, host: set ? "example.com" : null });

beforeEach(() => {
  getOwnBrowserQueue.mockReset().mockResolvedValue(null);
  runOwnBrowserNow.mockReset().mockResolvedValue(undefined);
  getDutyTriggers.mockReset().mockResolvedValue({ duties: [] });
  runDuty.mockReset();
  toastSuccess.mockReset();
  toastInfo.mockReset();
  toastApiError.mockReset();
});

describe("Run my own-browser tasks now", () => {
  it("is absent when no card needs the owner's Chrome", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue({ count: 0 }));
    render(<RunNowPanel />);
    await waitFor(() => expect(getOwnBrowserQueue).toHaveBeenCalled());
    expect(screen.queryByTestId("own-browser-run-now")).toBeNull();
  });

  it("POSTs run-now, toasts, and drops the copy-a-command step on success", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue());
    render(<RunNowPanel />);
    fireEvent.click(await screen.findByTestId("own-browser-run-now"));
    await waitFor(() => expect(runOwnBrowserNow).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Your own-browser tasks are starting"));
    expect(screen.queryByText(/run 360 batch/)).toBeNull();
  });

  it("a failed run-now toasts the error and shows no copy-a-command step", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue());
    runOwnBrowserNow.mockRejectedValue(new ApiError(404, "nope"));
    render(<RunNowPanel />);
    fireEvent.click(await screen.findByTestId("own-browser-run-now"));
    await waitFor(() => expect(toastApiError).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.queryByTestId("own-browser-run-now-done")).toBeNull();
    expect(screen.queryByText(/run 360 batch/)).toBeNull();
  });

  it("shows the queue count when the queue answers, in either shape", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue({ count: 10 }));
    const first = render(<RunNowPanel />);
    expect(await screen.findByTestId("own-browser-count")).toHaveTextContent("10 waiting for your own browser");
    first.unmount();
    getOwnBrowserQueue.mockResolvedValue(queue({ count: 1, open_now: true }));
    render(<RunNowPanel />);
    expect(await screen.findByTestId("own-browser-count")).toHaveTextContent("1 waiting for your own browser");
    expect(screen.getByTestId("own-browser-window")).toHaveTextContent("Open now");
  });

  it("hides the queue count when the queue fails", async () => {
    getDutyTriggers.mockResolvedValue({ duties: [duty("inbox", "Gmail check")] });
    render(<RunNowPanel />);
    await screen.findByTestId("run-now-inbox");
    expect(screen.queryByTestId("own-browser-count")).toBeNull();
  });

  it("RunChromeTasks reads neutrally in every state", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue({ run_now_until: new Date(Date.now() + 30 * 60000).toISOString() }));
    const { container } = render(<RunNowPanel />);
    expect(await screen.findByTestId("own-browser-open-for")).toHaveTextContent("Open for the next 30 minutes.");
    expect(container.textContent).not.toMatch(BAD);
    fireEvent.click(screen.getByTestId("own-browser-run-now"));
    await screen.findByTestId("own-browser-run-now-done");
    expect(container.textContent).not.toMatch(BAD);
  });
});

describe("OwnBrowserQueueCard", () => {
  it("shows nothing when nothing waits", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue({ count: 0 }));
    render(<RunNowPanel />);
    await waitFor(() => expect(getOwnBrowserQueue).toHaveBeenCalled());
    expect(screen.queryByTestId("own-browser-queue")).toBeNull();
  });

  it("shows title, count, window and runs now once", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue());
    render(<RunNowPanel />);
    expect(await screen.findByTestId("own-browser-window")).toHaveTextContent("Any time your PC is on");
    expect(screen.getByTestId("own-browser-queue")).toHaveTextContent("Waiting for your own browser");
    expect(screen.getByTestId("own-browser-count")).toHaveTextContent("2 waiting for your own browser");
    expect(screen.queryByTestId("own-browser-run-now-done")).toBeNull();
    fireEvent.click(screen.getByTestId("own-browser-run-now"));
    expect(await screen.findByTestId("own-browser-run-now-done")).toHaveTextContent(
      "Open for the next 60 minutes. Your assistant on your PC takes them one at a time.");
    expect(runOwnBrowserNow).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("own-browser-run-now")).toHaveTextContent("Run my own-browser tasks now");
  });

  it("window text: open now, opens later, closed", () => {
    expect(windowText({ open_now: true, opens_at: null, why: "pc_on_hours" })).toBe("Open now");
    expect(windowText({ open_now: false, opens_at: "2026-10-26T09:00:00", why: "closed" })).toMatch(/^Opens \w{3} 09:00$/);
    expect(windowText({ open_now: false, opens_at: null, why: "closed" })).toBe("Closed");
  });

  it("the whole card is neutral", async () => {
    getOwnBrowserQueue.mockResolvedValue(queue({ open_now: true }));
    getDutyTriggers.mockResolvedValue({
      duties: [duty("hunt", "Hunt"), duty("batch", "Apply"), duty("inbox", "Gmail check"), duty("reach", "People hunt", false)],
    });
    const { container } = render(<RunNowPanel />);
    await screen.findByTestId("own-browser-window");
    await screen.findByTestId("run-now-inbox");
    expect(container.textContent).not.toMatch(BAD);
  });
});

describe("RunNowPanel duties", () => {
  it("lists linked duties Gmail first, each with Run now, and points unlinked ones to Connect", async () => {
    getDutyTriggers.mockResolvedValue({
      duties: [duty("hunt", "Hunt"), duty("batch", "Apply"), duty("inbox", "Gmail check"), duty("reach", "People hunt", false)],
    });
    render(<RunNowPanel />);
    await screen.findByTestId("run-now-inbox");
    const ids = Array.from(screen.getByTestId("run-now-panel").querySelectorAll("li")).map((li) => li.getAttribute("data-testid"));
    expect(ids).toEqual(["run-now-inbox", "run-now-hunt", "run-now-batch"]);
    expect(screen.getByTestId("run-now-inbox-btn")).toHaveTextContent("Check Gmail now");
    expect(screen.getByTestId("run-now-inbox-btn")).toHaveAttribute("title", "Check my Gmail for replies");
    expect(screen.getByTestId("run-now-unlinked")).toHaveTextContent("People hunt: no start link yet.");
    expect(screen.getByRole("link", { name: "Add one in Connect your assistant" })).toHaveAttribute("href", "/connect");
  });

  it("Run now starts that one duty and says so; a refused start says why in plain words", async () => {
    getDutyTriggers.mockResolvedValue({ duties: [duty("inbox", "Gmail check")] });
    runDuty.mockResolvedValueOnce({ fired: true, duty: "inbox" }).mockResolvedValueOnce({ fired: false, reason: "cooldown" });
    render(<RunNowPanel />);
    fireEvent.click(await screen.findByTestId("run-now-inbox-btn"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Gmail check started."));
    expect(runDuty).toHaveBeenCalledWith("inbox");
    await waitFor(() => expect(screen.getByTestId("run-now-inbox-btn")).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("run-now-inbox-btn"));
    await waitFor(() => expect(toastInfo).toHaveBeenCalledWith("Started a moment ago. Try again in a minute."));
  });

  it("re-reads when the assistant moves anything", async () => {
    render(<RunNowPanel />);
    await waitFor(() => expect(getDutyTriggers).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new CustomEvent("job360:changes", { detail: { areas: ["other"], full: false, seq: 1 } }));
    await waitFor(() => expect(getDutyTriggers).toHaveBeenCalledTimes(2));
  });

  it("a failed queue read keeps the last good count and the Run now button", async () => {
    getOwnBrowserQueue.mockResolvedValueOnce(queue({ count: 3 })).mockResolvedValue(null);
    render(<RunNowPanel />);
    expect(await screen.findByTestId("own-browser-count")).toHaveTextContent("3 waiting for your own browser");
    window.dispatchEvent(new CustomEvent("job360:changes", { detail: { areas: ["other"], full: false, seq: 2 } }));
    await waitFor(() => expect(getOwnBrowserQueue).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("own-browser-count")).toHaveTextContent("3 waiting for your own browser");
    fireEvent.click(screen.getByTestId("own-browser-run-now"));
    expect(await screen.findByTestId("own-browser-run-now-done")).toBeInTheDocument();
  });
});
