import { beforeEach, describe, expect, it, vi } from "vitest";

const fireAssistant = vi.fn();
vi.mock("@/lib/api", () => ({ fireAssistant: (...a: unknown[]) => fireAssistant(...a) }));
const success = vi.fn();
const error = vi.fn();
const info = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    success: (m: string, o?: unknown) => success(m, o),
    error: (m: string) => error(m),
    info: (m: string) => info(m),
  },
}));

import { startAssistantAfterSend } from "../assistant-start";

beforeEach(() => {
  fireAssistant.mockReset();
  success.mockReset();
  error.mockReset();
  info.mockReset();
});

describe("startAssistantAfterSend", () => {
  it("a yes inside the cool-down is never silent: it says how to send it", async () => {
    fireAssistant.mockResolvedValue({ fired: false, reason: "cooldown", retry_after: 42 });
    await startAssistantAfterSend();
    expect(info).toHaveBeenCalledTimes(1);
    expect(info.mock.calls[0][0]).toMatch(/in 42 seconds press Start now/);
    expect(error).not.toHaveBeenCalled();
  });

  it("opens the started session without giving it this tab (noopener)", async () => {
    fireAssistant.mockResolvedValue({ fired: true, count: 2, session_url: "https://claude.ai/code/session_1" });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await startAssistantAfterSend();
    const action = (success.mock.calls[0][1] as { action: { onClick: () => void } }).action;
    action.onClick();
    expect(open).toHaveBeenCalledWith("https://claude.ai/code/session_1", "_blank", "noopener,noreferrer");
    open.mockRestore();
  });

  it("stays quiet after Send when nothing is set up or waiting", async () => {
    for (const reason of ["not_set", "nothing_approved"]) {
      fireAssistant.mockResolvedValue({ fired: false, reason });
      await startAssistantAfterSend();
    }
    expect(info).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
  });
});
