/**
 * Experience Level must not invent an answer for a brand-new account.
 *
 * THE BUG, as measured. A brand-new account has no profile row, so
 * GET /profile 404s, page.tsx keeps `profile = null`, and passes
 * `preferences={}` to this form. `prefsFromRaw` then substitutes "mid" for the
 * missing experience_level, and the first save posts it as though the user had
 * chosen it. The backend stores it verbatim -- `_apply_preferences` reads
 * experience_level with a bare `.get(..., "")` and normalises nothing.
 *
 * "mid" is not inert. resolve_experience_level treats a typed value as
 * authoritative ("typed always wins"), so seniority_score skips its neutral
 * midpoint and runs the real curve at SENIORITY_WEIGHT=8 -- up to 8 points
 * given or taken on every job. The same fake value is then stated to the LLM
 * judge and appended to the semantic vector.
 *
 * Rule #29: an unstated preference is silence. This is the identical defect
 * already fixed one field over for `work_arrangement`, whose "any" sentinel was
 * reaching the judge as a real constraint. That fix was never copied here.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

describe("PreferencesForm — experience level is never invented", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("a brand-new account does not save an experience level it never chose", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    // Exactly what page.tsx passes when GET /profile 404s: `profile?.preferences ?? {}`.
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);

    // Touch an UNRELATED field, the way a real user would while filling the form.
    const about = screen.getByPlaceholderText(/Experienced data scientist/i);
    fireEvent.change(about, { target: { value: "Looking for ML roles." } });

    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.experience_level).toBe("");
  });

  it("a real choice is still sent", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ experience_level: "senior" }}
        onSave={onSave}
        loading={false}
      />
    );

    const about = screen.getByPlaceholderText(/Experienced data scientist/i);
    fireEvent.change(about, { target: { value: "x" } });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave.mock.calls[0][0].experience_level).toBe("senior");
  });

  it("a stored empty value stays empty rather than becoming a default", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    // What the backend actually returns once any profile row exists.
    render(
      <PreferencesForm
        preferences={{ experience_level: "" }}
        onSave={onSave}
        loading={false}
      />
    );

    const about = screen.getByPlaceholderText(/Experienced data scientist/i);
    fireEvent.change(about, { target: { value: "y" } });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave.mock.calls[0][0].experience_level).toBe("");
  });
});
