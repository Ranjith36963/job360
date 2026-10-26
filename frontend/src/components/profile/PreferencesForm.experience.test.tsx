/**
 * Experience level and work arrangement left the CV tab (owner, 2026-10-26):
 * they are Memory -> Job targets rows now. This card has no control for them
 * and never sends them, so a stored answer is kept as it is and nothing is
 * ever invented for a brand-new account (rule #29).
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

  it("the CV tab has no level or work-arrangement control: they live in Memory, Job targets", () => {
    render(<PreferencesForm preferences={{ experience_level: "senior", work_arrangement: "remote" }} onSave={vi.fn()} loading={false} />);
    expect(screen.queryByText(/Experience Level/i)).toBeNull();
    expect(screen.queryByText(/Work Arrangement/i)).toBeNull();
  });

  it("a save never sends experience_level, so a real choice is kept as stored", async () => {
    // Never invented, never overwritten from here (rule #29).
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ experience_level: "senior" }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["experience_level"]) expect(sent).not.toHaveProperty(key);
  });

  it("a save never sends work_arrangement, so an empty one stays empty", async () => {
    // Empty = not chosen; nothing here turns it into a default.
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ work_arrangement: "" }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["work_arrangement"]) expect(sent).not.toHaveProperty(key);
  });
});
