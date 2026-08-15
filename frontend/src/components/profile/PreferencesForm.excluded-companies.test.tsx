/**
 * excluded_companies — a dead field, fully removed.
 *
 * VERIFIED before removing anything: a repo-wide grep for `excluded_companies`
 * turned up ZERO matches anywhere in backend/ — no route, no scorer, no model
 * field, no migration. The field was collected by this form (with no visible
 * UI control — it was already hidden 2026-08-08) and sent on every autosave,
 * discarded silently on arrival. This test pins the outcome the user's network
 * tab would show: the saved payload no longer carries a key nobody reads.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

describe("PreferencesForm — excluded_companies is not sent", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("omits excluded_companies from the autosave payload even when the stored profile has one", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        // A pre-existing profile with a stored value, to prove this isn't
        // passing only because the field was already empty.
        preferences={{ excluded_companies: ["Acme Corp"] }}
        onSave={onSave}
        loading={false}
      />
    );

    const about = screen.getByPlaceholderText(/Experienced data scientist/i);
    fireEvent.change(about, { target: { value: "Looking for backend roles." } });

    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect("excluded_companies" in sent).toBe(false);
  });
});
