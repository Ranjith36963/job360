import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const getDutyTriggers = vi.fn();
const saveDutyTrigger = vi.fn();
const deleteDutyTrigger = vi.fn();
const runDuty = vi.fn();
vi.mock("@/lib/api", () => ({
  getDutyTriggers: (...a: unknown[]) => getDutyTriggers(...a),
  saveDutyTrigger: (...a: unknown[]) => saveDutyTrigger(...a),
  deleteDutyTrigger: (...a: unknown[]) => deleteDutyTrigger(...a),
  runDuty: (...a: unknown[]) => runDuty(...a),
}));
const success = vi.fn();
const error = vi.fn();
const info = vi.fn();
vi.mock("sonner", () => ({
  toast: { success: (m: string) => success(m), error: (m: string) => error(m), info: (m: string) => info(m) },
}));

import { DUTY_ROUTINE_PROMPT, DutyStartLinksCard } from "../DutyStartLinksCard";

const unset = (duty: string, label: string) => ({ duty, label, set: false });
const HUNT_SET = {
  duty: "hunt", label: "Hunt", set: true, host: "api.anthropic.com", kind: "claude_routine", has_token: true,
  updated_at: "2026-10-25T10:00:00Z", last_fired_at: null, last_result: "",
};

beforeEach(() => {
  getDutyTriggers.mockReset().mockResolvedValue({
    duties: [HUNT_SET, unset("batch", "Apply"), unset("inbox", "Gmail check"), unset("reach", "People hunt")],
  });
  saveDutyTrigger.mockReset().mockResolvedValue({});
  deleteDutyTrigger.mockReset().mockResolvedValue({ deleted: true });
  runDuty.mockReset().mockResolvedValue({ fired: true, duty: "hunt" });
  success.mockReset();
  error.mockReset();
  info.mockReset();
});

describe("Start each job at once", () => {
  it("shows one row per duty in plain words and never a token", async () => {
    render(<DutyStartLinksCard />);
    for (const label of ["Hunt", "Apply", "Gmail check", "People hunt"]) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    expect(screen.getByTestId("duty-hunt-set")).toHaveTextContent("api.anthropic.com (token saved)");
    expect(screen.getByTestId("duty-reach")).toHaveTextContent("Not set: it waits for your hourly run.");
    expect(DUTY_ROUTINE_PROMPT).toContain("whats_due");
    expect(DUTY_ROUTINE_PROMPT).toContain("never my yes to submit");
  });

  it("Run now and Test start that duty; a held start says why", async () => {
    render(<DutyStartLinksCard />);
    const row = await screen.findByTestId("duty-hunt");
    const runNow = within(row).getByRole("button", { name: "Run now" });
    expect(runNow).toHaveAttribute("data-slot", "button");
    fireEvent.click(runNow);
    await waitFor(() => expect(runDuty).toHaveBeenCalledWith("hunt", false));
    expect(success).toHaveBeenCalledWith("Hunt started.");
    runDuty.mockResolvedValueOnce({ fired: false, reason: "cooldown" });
    fireEvent.click(within(row).getByRole("button", { name: "Test" }));
    await waitFor(() => expect(runDuty).toHaveBeenCalledWith("hunt", true));
    await waitFor(() => expect(info).toHaveBeenCalledWith("Started a moment ago. Try again in a minute."));
  });

  it("saves a link and token for one duty and clears the fields", async () => {
    render(<DutyStartLinksCard />);
    const row = await screen.findByTestId("duty-reach");
    fireEvent.change(within(row).getByLabelText("People hunt start link"), { target: { value: " https://x.example/fire " } });
    fireEvent.change(within(row).getByLabelText("People hunt token"), { target: { value: "tok" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveDutyTrigger).toHaveBeenCalledWith("reach", "https://x.example/fire", "tok"));
    await waitFor(() => expect(within(row).getByLabelText("People hunt token")).toHaveValue(""));
  });
});
