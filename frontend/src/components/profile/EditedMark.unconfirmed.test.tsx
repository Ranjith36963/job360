import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EditedMark } from "./EditedMark";
import type { AgentEdit } from "@/lib/agent-edits";

function edit(over: Record<string, unknown> = {}): AgentEdit {
  return {
    path: "preferences.salary_min",
    value: 50000,
    previous_value: 45000,
    set_by: "agent:Claude",
    set_at: "2026-09-25T10:00:00Z",
    source: "agent_inferred",
    confirmed: false,
    ...over,
  } as AgentEdit;
}

describe("EditedMark unconfirmed", () => {
  it("shows the unconfirmed badge and reads Confirm instead of Keep", async () => {
    const onKeep = vi.fn().mockResolvedValue(undefined);
    render(<EditedMark edit={edit()} onKeep={onKeep} />);
    expect(screen.getByTestId("unconfirmed-badge")).toBeInTheDocument();
    expect(screen.getByTestId("keep").textContent).toBe("Confirm");
    fireEvent.click(screen.getByTestId("keep"));
    await waitFor(() => expect(onKeep).toHaveBeenCalledWith("preferences.salary_min"));
  });

  it("a confirmed edit has no badge and still says Keep", () => {
    render(<EditedMark edit={edit({ source: "user_said", confirmed: true })} onKeep={vi.fn()} />);
    expect(screen.queryByTestId("unconfirmed-badge")).toBeNull();
    expect(screen.getByTestId("keep").textContent).toBe("Keep");
  });
});
