import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Only the HTTP door is mocked: the real runOwnBrowserNow / getOwnBrowserQueue run, so the URL and method asserted are the ones the button uses.
const requestJson = vi.fn();
const runDuty = vi.fn();
let duties: unknown[] = [];
vi.mock("@/lib/api", () => ({
  requestJson: (...a: unknown[]) => requestJson(...a),
  getDutyTriggers: async () => ({ duties }),
  runDuty: (...a: unknown[]) => runDuty(...a),
}));
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), info: vi.fn(), apiError: vi.fn() } }));

import { RunNowPanel } from "./RunNowPanel";

const queue = { count: 2, open_now: false, opens_at: null, why: "any_time", tasks: [], pc_on_hours: null, run_now_until: null };

beforeEach(() => {
  duties = [];
  runDuty.mockReset();
  requestJson.mockReset().mockImplementation(async (path: string, method: string) => (method === "GET" && path === "/api/own-browser/queue" ? queue : {}));
});

describe("Run my assistant-browser tasks now", () => {
  it("the button calls POST /api/own-browser/run-now", async () => {
    render(<RunNowPanel />);
    const button = await screen.findByRole("button", { name: "Run my assistant-browser tasks now" });
    fireEvent.click(button);
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith("/api/own-browser/run-now", "POST"));
    expect(requestJson.mock.calls.filter((c) => c[0] === "/api/own-browser/run-now")).toHaveLength(1);
  });

  it("with an assistant-browser start link saved, the same press starts it: run-now once, no second fire", async () => {
    // The server starts the saved link inside POST /api/own-browser/run-now (#1650; backend test
    // test_cv_own_browser_start_links asserts the start), so the page must not fire it again.
    duties = [{ duty: "own_browser", label: "Assistant browser (your Chrome)", set: true }];
    render(<RunNowPanel />);
    const button = await screen.findByRole("button", { name: "Run my assistant-browser tasks now" });
    fireEvent.click(button);
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith("/api/own-browser/run-now", "POST"));
    expect(requestJson.mock.calls.filter((c) => c[0] === "/api/own-browser/run-now")).toHaveLength(1);
    expect(runDuty).not.toHaveBeenCalled();
  });
});
