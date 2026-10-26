/**
 * PreferencesForm — visa answers left the CV tab (owner, 2026-10-26).
 *
 * needs_visa, the countries where the user can already work, and salary now
 * live in Memory -> Job targets (the "Where you'll work" grid). This card has
 * no control for them and never sends them, so the server keeps each one as
 * stored and a stale copy here can never overwrite a newer answer.
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

  it("the CV tab has no visa control: visa answers live in Memory, Job targets", () => {
    render(<PreferencesForm preferences={{ needs_visa: true }} onSave={vi.fn()} loading={false} />);
    expect(screen.queryByLabelText(VISA_LABEL)).toBeNull();
    expect(screen.queryByTestId("work-authorization-countries")).toBeNull();
  });

  it("a save never sends needs_visa, so a stored answer is kept as it is", async () => {
    // The server keeps an omitted key as stored; a stale copy here can never overwrite it.
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ needs_visa: true }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["needs_visa"]) expect(sent).not.toHaveProperty(key);
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

  it("a save never sends the countries where the user can already work", async () => {
    // Kept as stored; edited in Memory (Job targets) only.
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ work_authorization_countries: ["GB", "IN"] }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["work_authorization_countries"]) expect(sent).not.toHaveProperty(key);
  });
});

// docs/product/VISION.md decision 27 — Fact 2, the candidate's
// work-authorization countries (compared against a job's visa_country
// server-side; Job360 never knows country rules itself, rule #29). A
// searchable country picker (owner-approved copy, 2026-09-27) replaced the
// old free-typed ISO-code box — the wire value is still the alpha-2 code.
describe("PreferencesForm — work authorization countries", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("a save never sends salary, so the stored range is kept", async () => {
    // Salary is set per country in Memory (Job targets).
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{ salary_min: 50000, salary_max: 70000 }} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/), {
      target: { value: "Data engineer" },
    });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0];
    expect(sent.about_me).toBe("Data engineer");
    for (const key of ["salary_min", "salary_max", "salary_by_country"]) expect(sent).not.toHaveProperty(key);
  });
});
