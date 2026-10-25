import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const getScheduleStatus = vi.fn();
vi.mock("@/lib/api", () => ({
  getScheduleStatus: (...a: unknown[]) => getScheduleStatus(...a),
}));

import { DutyStatus, stateText } from "./DutyStatus";

const row = (over: Record<string, unknown>) => ({
  duty: "hunt", label: "Hunt", state: "waiting", failures_in_row: 0, ...over,
}) as Parameters<typeof stateText>[0];

describe("DutyStatus", () => {
  it("shows one line per job that is on, with how it last ended", async () => {
    getScheduleStatus.mockResolvedValue({ duties: [
      row({ last_run_at: new Date().toISOString(), last_outcome: "failed", state: "failing", failures_in_row: 2 }),
      row({ duty: "inbox", label: "Gmail check", state: "off" }),
      row({ duty: "batch", label: "Apply", state: "held", hold: "queue_empty" }),
    ] });
    render(<DutyStatus />);
    const hunt = await screen.findByTestId("duty-status-hunt");
    expect(hunt).toHaveTextContent("Last run just now: failed. Failed 2 times in a row.");
    expect(screen.getByTestId("duty-status-batch")).toHaveTextContent("Not run yet. Waiting: nothing is waiting in your queue.");
    expect(screen.queryByTestId("duty-status-inbox")).toBeNull();
  });

  it("stays silent when every job is off or the status cannot load", async () => {
    getScheduleStatus.mockRejectedValue(new Error("500"));
    const { container } = render(<DutyStatus />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });

  it("says plainly what a running or full-queue job is doing", () => {
    expect(stateText(row({ state: "running" }))).toBe("Running now.");
    expect(stateText(row({ state: "held", hold: "queue_full" }))).toBe("Waiting: your queue is full, so it waits until it drains.");
    expect(stateText(row({ duty: "batch", state: "held", hold: "waiting_on_you" }))).toBe(
      "Waiting: every job in your queue is waiting on you (see Needs you).",
    );
  });
});
