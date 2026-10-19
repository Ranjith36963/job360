import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const getAssistantTrigger = vi.fn();
const saveAssistantTrigger = vi.fn();
const deleteAssistantTrigger = vi.fn();
vi.mock("@/lib/api", () => ({
  getAssistantTrigger: (...a: unknown[]) => getAssistantTrigger(...a),
  saveAssistantTrigger: (...a: unknown[]) => saveAssistantTrigger(...a),
  deleteAssistantTrigger: (...a: unknown[]) => deleteAssistantTrigger(...a),
}));
const success = vi.fn();
const error = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => success(m), error: (m: string) => error(m) } }));

import { PC_LOOP_COMMAND, ROUTINE_PROMPT, SendStartsAssistantCard } from "../SendStartsAssistantCard";

const SET = {
  set: true, host: "api.anthropic.com", kind: "claude_routine", has_token: true,
  updated_at: "2026-10-19T10:00:00Z", last_fired_at: "2026-10-19T11:00:00Z", last_result: "started",
};

beforeEach(() => {
  getAssistantTrigger.mockReset().mockResolvedValue({ set: false });
  saveAssistantTrigger.mockReset().mockResolvedValue(SET);
  deleteAssistantTrigger.mockReset().mockResolvedValue({ deleted: true });
  success.mockReset();
  error.mockReset();
});

describe("Start my assistant when I press Send", () => {
  it("gives the routine instructions and the PC fallback, both naming the hard lines", async () => {
    render(<SendStartsAssistantCard />);
    expect(await screen.findByTestId("routine-prompt")).toHaveTextContent("routine-fire-payload");
    for (const text of [ROUTINE_PROMPT, PC_LOOP_COMMAND]) {
      expect(text).toMatch(/check_submit|user_approved/);
      expect(text).toMatch(/Never type a password/);
    }
    expect(screen.getByTestId("pc-loop-command")).toHaveTextContent("/loop 5m");
  });

  it("saves the link and token, then clears the token box and never shows it", async () => {
    render(<SendStartsAssistantCard />);
    fireEvent.change(await screen.findByLabelText(/Start link/), {
      target: { value: " https://api.anthropic.com/v1/claude_code/routines/trig_1/fire " },
    });
    const token = screen.getByLabelText(/Token/);
    expect(token).toHaveAttribute("type", "password");
    fireEvent.change(token, { target: { value: "sk-ant-oat01-x" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(saveAssistantTrigger).toHaveBeenCalledWith(
        "https://api.anthropic.com/v1/claude_code/routines/trig_1/fire", "sk-ant-oat01-x",
      ),
    );
    expect(await screen.findByTestId("send-trigger-set")).toHaveTextContent("your Claude routine at api.anthropic.com");
    expect(screen.getByLabelText(/Token/)).toHaveValue("");
    expect(document.body.textContent).not.toContain("sk-ant-oat01-x");
  });

  it("shows the last start and removes it", async () => {
    getAssistantTrigger.mockResolvedValue(SET);
    render(<SendStartsAssistantCard />);
    expect(await screen.findByTestId("send-trigger-set")).toHaveTextContent("Last start: started");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(deleteAssistantTrigger).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByTestId("send-trigger-set")).toBeNull());
  });
});
