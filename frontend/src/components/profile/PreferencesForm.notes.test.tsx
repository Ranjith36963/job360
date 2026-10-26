/**
 * PreferencesForm — "Things your AI assistant should know" lives on Home and in
 * Memory's step facts, never on Profile. A Profile save never sends the notes:
 * the server keeps an omitted key as stored, so facts added elsewhere are never
 * overwritten by this tab's older copy.
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

  it("does not show the notes on Profile", () => {
    render(
      <PreferencesForm
        preferences={{ assistant_notes: ["Never apply to agencies"] }}
        onSave={vi.fn()}
        loading={false}
      />
    );
    expect(screen.queryByText(/should know/i)).toBeNull();
    expect(screen.queryByText("Never apply to agencies")).toBeNull();
    expect(screen.queryByTestId("assistant-notes")).toBeNull();
  });

  it("a Profile save never sends the notes, so facts added in Memory or on Home are kept", async () => {
    // Before, a save sent this tab's copy of the notes and could overwrite newer ones.
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ assistant_notes: ["Never apply to agencies", "About you: I go by Alex."] }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["assistant_notes"]) expect(sent).not.toHaveProperty(key);
  });

  it("hydration alone never saves", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm preferences={{ assistant_notes: ["Keep"] }} onSave={onSave} loading={false} />
    );
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(onSave).not.toHaveBeenCalled();
  });
});
