import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssistantNotes } from "@/components/profile/AssistantNotes";

const api = vi.hoisted(() => ({ getProfileEditHistory: vi.fn() }));
vi.mock("@/lib/api", () => api);

const row = (value: unknown, set_by = "web", set_at = "2026-10-18T10:00:00Z") => ({
  path: "preferences.assistant_notes",
  value,
  set_by,
  set_at,
  previous_value: null,
});

beforeEach(() => vi.clearAllMocks());

describe("AssistantNotes — edit a line", () => {
  it("Enter saves the reworded line in place, once", () => {
    const onChange = vi.fn();
    render(<AssistantNotes notes={["Never apply to agencies", "No night shifts"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Change note: Never apply to agencies" }));
    const input = screen.getByLabelText("Edit note: Never apply to agencies");
    fireEvent.change(input, { target: { value: "  Never apply to recruitment agencies " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(["Never apply to recruitment agencies", "No night shifts"]);
  });

  it("clicking the line itself starts editing; clicking away saves", () => {
    const onChange = vi.fn();
    render(<AssistantNotes notes={["A line"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit note: A line" }));
    const input = screen.getByLabelText("Edit note: A line");
    fireEvent.change(input, { target: { value: "A better line" } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith(["A better line"]);
  });

  it("Escape, an empty edit or a copy of another line changes nothing", () => {
    const onChange = vi.fn();
    render(<AssistantNotes notes={["One", "Two"]} onChange={onChange} />);
    for (const value of ["Reworded", "   ", "two"]) {
      fireEvent.click(screen.getByRole("button", { name: "Change note: One" }));
      const input = screen.getByLabelText("Edit note: One");
      fireEvent.change(input, { target: { value } });
      fireEvent.keyDown(input, { key: value === "Reworded" ? "Escape" : "Enter" });
    }
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("AssistantNotes — history", () => {
  it("has no History button until asked for", () => {
    render(<AssistantNotes notes={["A"]} onChange={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /history/i })).toBeNull();
  });

  it("lists lines you once had and adds one back", async () => {
    api.getProfileEditHistory.mockResolvedValue([
      row(["Keep me"], "web", "2026-10-19T09:00:00Z"),
      row(["Keep me", "Never skip a job because of a CAPTCHA"], "agent:Claude", "2026-10-18T09:00:00Z"),
    ]);
    const onChange = vi.fn();
    render(<AssistantNotes notes={["Keep me"]} onChange={onChange} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "History of things your assistant should know" }));
    const rows = await screen.findAllByTestId("notes-history-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Never skip a job because of a CAPTCHA");
    expect(rows[0]).toHaveTextContent("Claude");
    expect(api.getProfileEditHistory).toHaveBeenCalledWith("preferences.assistant_notes");
    fireEvent.click(screen.getByRole("button", { name: "Add back: Never skip a job because of a CAPTCHA" }));
    expect(onChange).toHaveBeenCalledWith(["Keep me", "Never skip a job because of a CAPTCHA"]);
  });

  it("a line removed in this view shows up at once, before the log has it", async () => {
    api.getProfileEditHistory.mockResolvedValue([]);
    const { rerender } = render(<AssistantNotes notes={["Keep", "Oops"]} onChange={vi.fn()} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "Remove note: Oops" }));
    rerender(<AssistantNotes notes={["Keep"]} onChange={vi.fn()} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "History of things your assistant should know" }));
    await waitFor(() => expect(screen.getByTestId("notes-history")).toHaveTextContent("Oops"));
  });

  it("says so when nothing was ever removed", async () => {
    api.getProfileEditHistory.mockResolvedValue([row(["Keep"])]);
    render(<AssistantNotes notes={["Keep"]} onChange={vi.fn()} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "History of things your assistant should know" }));
    expect(await screen.findByText("Nothing removed yet.")).toBeInTheDocument();
  });

  it("removing the same line twice lists it once", async () => {
    api.getProfileEditHistory.mockResolvedValue([]);
    const { rerender } = render(<AssistantNotes notes={["Keep", "X"]} onChange={vi.fn()} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "Remove note: X" }));
    rerender(<AssistantNotes notes={["Keep", "X"]} onChange={vi.fn()} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "Remove note: X" }));
    rerender(<AssistantNotes notes={["Keep"]} onChange={vi.fn()} showHistory />);
    fireEvent.click(screen.getByRole("button", { name: "History of things your assistant should know" }));
    await waitFor(() => expect(screen.getAllByTestId("notes-history-row")).toHaveLength(1));
  });
});
