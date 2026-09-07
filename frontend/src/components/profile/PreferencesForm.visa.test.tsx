/**
 * PreferencesForm — visa sponsorship control.
 *
 * needs_visa is stored on the profile so the user's own agent knows whether
 * sponsorship is required when it judges a job's fit. It had NO UI control
 * anywhere until 2026-08-08, so the field was always the default False and
 * the agent had no way to know a user needed one. These tests pin the
 * round-trip: the checkbox must hydrate from a saved value AND send its new
 * value on change — and, per the serialize/hydrate symmetry the component
 * depends on, hydration alone must NOT fire a save.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

const VISA_LABEL = /i need visa sponsorship/i;

describe("PreferencesForm — visa sponsorship", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("renders a visa control (the dimension had no front door before)", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);
    expect(screen.getByLabelText(VISA_LABEL)).toBeTruthy();
  });

  it("hydrates as checked from a saved needs_visa=true", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ needs_visa: true }}
        onSave={onSave}
        loading={false}
      />
    );
    expect((screen.getByLabelText(VISA_LABEL) as HTMLInputElement).checked).toBe(true);
  });

  it("does NOT save on hydration (serialize/hydrate symmetry)", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ needs_visa: true }}
        onSave={onSave}
        loading={false}
      />
    );
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(onSave).not.toHaveBeenCalled();
  });

  it("sends needs_visa=true when the user ticks the box", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);

    fireEvent.click(screen.getByLabelText(VISA_LABEL));
    expect(onSave).not.toHaveBeenCalled(); // debounced

    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ needs_visa: true })
    );
  });
});
