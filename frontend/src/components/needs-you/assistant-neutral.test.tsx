import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ApiError } from "@/lib/api-error";

const requestJson = vi.fn();
vi.mock("@/lib/api", () => ({
  requestJson: (...a: unknown[]) => requestJson(...a),
  getAssistantTrigger: async () => ({ set: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { RunChromeTasks } from "./RunChromeTasks";
import { OwnBrowserQueueCard, windowText } from "./OwnBrowserQueueCard";
import { PC_LOOP_COMMAND, SendStartsAssistantCard } from "@/components/connect/SendStartsAssistantCard";

const BAD = /chrome|claude/i;
const src = (p: string) => readFileSync(join(process.cwd(), "src", p), "utf-8");

/** What a user can read in a component file: code with comments, imports, test ids and identifiers removed. */
function visibleStrings(file: string): string {
  return src(file)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/^import .*$/gm, "")
    .replace(/data-testid=(\{`[^`]*`\}|"[^"]*")/g, "")
    .replace(/testid="[^"]*"/g, "")
    .replace(/\b\w*(Chrome|OWN_CHROME)\w*\b/g, "");
}

const queue = { count: 2, open_now: false, opens_at: null, why: "any_time", tasks: [], pc_on_hours: null, run_now_until: null } as const;

beforeEach(() => requestJson.mockReset().mockResolvedValue({}));

describe("assistant-neutral strings (owner 2026-10-25)", () => {
  it.each([
    "components/needs-you/JobCard.tsx",
    "components/needs-you/RunChromeTasks.tsx",
    "components/needs-you/OwnBrowserQueueCard.tsx",
    "components/needs-you/PersonRow.tsx",
    "lib/browser-labels.ts",
  ])("%s has no Chrome or Claude in any string", (file) => {
    expect(visibleStrings(file)).not.toMatch(BAD);
  });

  it("the SendStartsAssistantCard fallback command and its own-computer text have none", async () => {
    expect(PC_LOOP_COMMAND).not.toMatch(BAD);
    render(<SendStartsAssistantCard />);
    const details = (await screen.findByText(/No cloud\?/)).closest("details");
    expect(details?.textContent).not.toMatch(BAD);
    expect(details?.textContent).toContain("your own browser");
  });

  it("RunChromeTasks reads neutrally in every state", async () => {
    const { container, unmount } = render(<RunChromeTasks />);
    expect(container.textContent).not.toMatch(BAD);
    fireEvent.click(screen.getByTestId("run-chrome-tasks"));
    await screen.findByTestId("run-chrome-panel");
    expect(container.textContent).not.toMatch(BAD);
    unmount();
    requestJson.mockImplementation(async (path: string) => {
      if (path === "/api/own-browser/run-now") throw new ApiError(404, "no");
      return {};
    });
    const r2 = render(<RunChromeTasks />);
    fireEvent.click(screen.getByTestId("run-chrome-tasks"));
    await waitFor(() => expect(r2.container.textContent).toContain("open your assistant in your own browser and say:"));
    expect(r2.container.textContent).not.toMatch(BAD);
  });
});

describe("OwnBrowserQueueCard", () => {
  it("shows nothing when nothing waits", () => {
    const { container } = render(<OwnBrowserQueueCard queue={{ ...queue, count: 0 }} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows title, count, window and runs now once", async () => {
    render(<OwnBrowserQueueCard queue={{ ...queue }} />);
    expect(screen.getByTestId("own-browser-queue")).toHaveTextContent("Waiting for your own browser");
    expect(screen.getByTestId("own-browser-count")).toHaveTextContent("2 waiting for your own browser");
    expect(screen.getByTestId("own-browser-window")).toHaveTextContent("Any time your PC is on");
    expect(screen.queryByTestId("own-browser-run-now-done")).toBeNull();
    fireEvent.click(screen.getByTestId("own-browser-run-now"));
    await waitFor(() => expect(requestJson).toHaveBeenCalledWith("/api/own-browser/run-now", "POST"));
    expect(await screen.findByTestId("own-browser-run-now-done")).toHaveTextContent(
      "Open for the next 60 minutes. Your assistant on your PC takes them one at a time.");
    expect(screen.getByTestId("own-browser-run-now")).toHaveTextContent("Run my own-browser tasks now");
  });

  it("window text: open now, opens later, closed", () => {
    expect(windowText({ open_now: true, opens_at: null, why: "pc_on_hours" })).toBe("Open now");
    expect(windowText({ open_now: false, opens_at: "2026-10-26T09:00:00", why: "closed" })).toMatch(/^Opens \w{3} 09:00$/);
    expect(windowText({ open_now: false, opens_at: null, why: "closed" })).toBe("Closed");
  });

  it("the whole card is neutral", () => {
    const { container } = render(<OwnBrowserQueueCard queue={{ ...queue }} />);
    expect(container.textContent).not.toMatch(BAD);
  });
});
