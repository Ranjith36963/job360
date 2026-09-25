/**
 * EditedMark — "Changed by <name> · was <previous>" + Take back (owner
 * decision, 2026-09-25). Only an ASSISTANT's edit renders a mark; the
 * human's own web saves never do.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EditedMark } from "./EditedMark";
import type { AgentEdit } from "@/lib/agent-edits";

function edit(over: Partial<AgentEdit> = {}): AgentEdit {
  return {
    path: "preferences.salary_min",
    value: 50000,
    previous_value: 45000,
    set_by: "agent:Claude",
    set_at: "2026-09-25T10:00:00Z",
    ...over,
  };
}

describe("EditedMark", () => {
  it("names the assistant and the value before its change", () => {
    render(<EditedMark edit={edit()} />);
    expect(screen.getByTestId("agent-edit-mark").textContent).toBe(
      "Changed by Claude · was £45k"
    );
  });

  it("formats a list as comma-joined and nothing as empty", () => {
    const { rerender } = render(
      <EditedMark
        edit={edit({ path: "preferences.preferred_locations", previous_value: ["London", "Leeds"] })}
      />
    );
    expect(screen.getByTestId("agent-edit-mark").textContent).toContain("was London, Leeds");
    rerender(<EditedMark edit={edit({ path: "cv_data.location", previous_value: "" })} />);
    expect(screen.getByTestId("agent-edit-mark").textContent).toContain("was empty");
    rerender(<EditedMark edit={edit({ previous_value: null, set_by: "token:cli" })} />);
    expect(screen.getByTestId("agent-edit-mark").textContent).toBe("Changed by cli · was empty");
  });

  it("renders nothing for no edit and for the human's own web row", () => {
    const { container, rerender } = render(<EditedMark edit={undefined} />);
    expect(container.innerHTML).toBe("");
    rerender(<EditedMark edit={edit({ set_by: "web" })} />);
    expect(container.innerHTML).toBe("");
  });

  it("Take back calls back with the edited path", async () => {
    const onTakeBack = vi.fn().mockResolvedValue(undefined);
    render(<EditedMark edit={edit()} onTakeBack={onTakeBack} />);
    fireEvent.click(screen.getByRole("button", { name: "Take back Claude's change" }));
    await waitFor(() => expect(onTakeBack).toHaveBeenCalledWith("preferences.salary_min"));
  });

  it("shows no Take back or Keep button without a handler", () => {
    render(<EditedMark edit={edit()} />);
    expect(screen.queryByTestId("take-back")).toBeNull();
    expect(screen.queryByTestId("keep")).toBeNull();
  });

  it("Keep calls back with the edited path; the label is unchanged, Keep sits before Take back", async () => {
    const onKeep = vi.fn().mockResolvedValue(undefined);
    const onTakeBack = vi.fn().mockResolvedValue(undefined);
    render(<EditedMark edit={edit()} onKeep={onKeep} onTakeBack={onTakeBack} />);
    expect(screen.getByTestId("agent-edit-mark").textContent).toBe("Changed by Claude · was £45k");
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons).toEqual(["Keep", "Take back"]);
    fireEvent.click(screen.getByRole("button", { name: "Keep Claude's change" }));
    await waitFor(() => expect(onKeep).toHaveBeenCalledWith("preferences.salary_min"));
    expect(onTakeBack).not.toHaveBeenCalled();
  });
});
