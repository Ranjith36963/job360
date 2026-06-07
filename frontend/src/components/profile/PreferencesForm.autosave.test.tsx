/**
 * PreferencesForm auto-save.
 *
 * Preferences should persist the same way the CV/LinkedIn/GitHub inputs do —
 * automatically, with no "Save" button. Editing a field schedules a debounced
 * save; loading the form (hydration from props) must NOT trigger a save.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

const initial = {
  target_job_titles: ["ML Engineer"],
  additional_skills: ["python"],
  about_me: "",
};

describe("PreferencesForm — auto-save", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("has no manual 'Save Preferences' button", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={initial} onSave={onSave} loading={false} />);
    expect(
      screen.queryByRole("button", { name: /save preferences/i })
    ).toBeNull();
  });

  it("does NOT save on initial hydration from props", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={initial} onSave={onSave} loading={false} />);
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(onSave).not.toHaveBeenCalled();
  });

  it("auto-saves (debounced) after the user edits a field", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={initial} onSave={onSave} loading={false} />);

    const about = screen.getByPlaceholderText(/Experienced data scientist/i);
    fireEvent.change(about, { target: { value: "Senior ML engineer, 7 years." } });

    // Debounced — nothing fires immediately.
    expect(onSave).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ about_me: "Senior ML engineer, 7 years." })
    );
  });

  it("debounces rapid edits into a single save", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={initial} onSave={onSave} loading={false} />);

    const about = screen.getByPlaceholderText(/Experienced data scientist/i);
    fireEvent.change(about, { target: { value: "a" } });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    fireEvent.change(about, { target: { value: "ab" } });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    fireEvent.change(about, { target: { value: "abc" } });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ about_me: "abc" })
    );
  });
});
