/**
 * PreferencesForm — "Things your assistant should know" (owner decision,
 * 2026-09-25). Standing instructions, one line each, saved through the normal
 * debounced preferences save as `assistant_notes`. Empty = nothing to say
 * (rule #29): hydration alone never saves.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

describe("PreferencesForm — assistant notes", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("shows the saved notes and does not save on hydration", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ assistant_notes: ["Never apply to agencies"] }}
        onSave={onSave}
        loading={false}
      />
    );
    expect(screen.getByText("Things your assistant should know")).toBeTruthy();
    expect(screen.getByText("Never apply to agencies")).toBeTruthy();
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(onSave).not.toHaveBeenCalled();
  });

  it("adds a line and saves the full list", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ assistant_notes: ["Never apply to agencies"] }}
        onSave={onSave}
        loading={false}
      />
    );
    const input = screen.getByLabelText("New note for your assistant");
    fireEvent.change(input, { target: { value: "  On holiday until 3 Oct " } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(screen.getByText("On holiday until 3 Oct")).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        assistant_notes: ["Never apply to agencies", "On holiday until 3 Oct"],
      })
    );
  });

  it("removes a line and saves the list without it", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ assistant_notes: ["Keep", "Drop me"] }}
        onSave={onSave}
        loading={false}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove note: Drop me" }));
    expect(screen.queryByText("Drop me")).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ assistant_notes: ["Keep"] })
    );
  });

  it("an empty note is never added", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);
    expect(
      (screen.getByRole("button", { name: "Add note" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("New note for your assistant"), {
      target: { value: "   " },
    });
    fireEvent.keyDown(screen.getByLabelText("New note for your assistant"), { key: "Enter" });
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(onSave).not.toHaveBeenCalled();
  });
});
