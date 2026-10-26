import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

describe("PreferencesForm - what you're looking for", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("the CV tab has no job-target controls: they live in Memory, Job targets", () => {
    render(<PreferencesForm preferences={{ target_job_titles: ["Data Engineer"], looking_for: ["small team"] }} onSave={vi.fn()} loading={false} />);
    expect(screen.queryByTestId("looking-for")).toBeNull();
    expect(screen.queryByTestId("years-min")).toBeNull();
    expect(screen.queryByText("Data Engineer")).toBeNull();
    expect(screen.queryByText(/Preferred Locations/i)).toBeNull();
  });

  it("a save never sends the job targets, so values edited in Memory are kept", async () => {
    // A stale copy on this tab can never overwrite them.
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ target_job_titles: ["Data Engineer"], preferred_locations: ["London"], looking_for: ["small team"], dealbreakers: ["on-call"], avoid_companies: ["Acme"], experience_years_min: 2, experience_years_max: 5 }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["target_job_titles", "preferred_locations", "looking_for", "dealbreakers", "avoid_companies", "experience_years_min", "experience_years_max"]) expect(sent).not.toHaveProperty(key);
  });
});
