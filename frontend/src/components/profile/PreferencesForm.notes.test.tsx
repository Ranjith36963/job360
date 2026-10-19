/**
 * PreferencesForm — "Things your AI assistant should know" lives on Home only
 * (owner, 2026-10-19). Profile no longer shows the notes, but every Profile
 * save still sends them back unchanged: the save replaces the whole
 * preferences block, so dropping them here would wipe them.
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

  it("a Profile save keeps the notes exactly as stored", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ assistant_notes: ["Never apply to agencies", "No night shifts"] }}
        onSave={onSave}
        loading={false}
      />
    );
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        about_me: "Data engineer",
        assistant_notes: ["Never apply to agencies", "No night shifts"],
      })
    );
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
