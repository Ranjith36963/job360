/**
 * Suggested skills, and the "words to avoid" control.
 *
 * The extractor proposes adjacent skills. They used to render further down the
 * profile page as plain <li> chips with no click handler, under helper text
 * telling the user to retype them by hand into Additional Skills. So the
 * feature's whole value — "we found skills you probably have" — cost the user
 * a manual copy each time, and most people simply never did it.
 *
 * They now live INSIDE this form, next to Additional Skills, and a tap adds
 * one. That placement is the load-bearing part, not a cosmetic choice:
 *
 *   - a tap only changes LOCAL state, so the form's existing debounced save
 *     persists it, and five taps cost ONE save. Handling the tap in the parent
 *     page would mean one save per chip — and every preferences save triggers a
 *     paid re-extraction, rate-limited to 12/hour.
 *   - the parent re-renders PreferencesForm with a BRAND-NEW `preferences`
 *     object after any save (the backend returns `asdict(...)`), which re-fires
 *     the hydration effect and overwrites every local field. A parent-side
 *     accept handler would therefore wipe whatever the user was part-way
 *     through typing elsewhere in the form.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

const initial = {
  target_job_titles: ["ML Engineer"],
  additional_skills: ["Python"],
  about_me: "",
};

describe("PreferencesForm — suggested skills", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("renders nothing when there are no suggestions", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={[]}
        onSave={onSave}
        loading={false}
      />
    );
    expect(screen.queryByText(/skills that often go with yours/i)).toBeNull();
  });

  it("shows a suggestion as a real, tappable control", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={["Kubernetes"]}
        onSave={onSave}
        loading={false}
      />
    );
    // getByRole("button"), not getByText: the entire defect was that these
    // rendered as inert list items. A <li> would pass a text query and fail
    // the user.
    expect(
      screen.getByRole("button", { name: /add kubernetes to additional skills/i })
    ).toBeTruthy();
  });

  it("tapping a suggestion adds it and auto-saves once", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={["Kubernetes"]}
        onSave={onSave}
        loading={false}
      />
    );

    fireEvent.click(
      screen.getByRole("button", { name: /add kubernetes to additional skills/i })
    );
    expect(onSave).not.toHaveBeenCalled(); // debounced, like every other edit

    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ additional_skills: ["Python", "Kubernetes"] })
    );
  });

  it("accepting several suggestions costs ONE save, not one per tap", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={["Kubernetes", "Terraform", "Airflow"]}
        onSave={onSave}
        loading={false}
      />
    );

    for (const skill of ["Kubernetes", "Terraform", "Airflow"]) {
      fireEvent.click(
        screen.getByRole("button", {
          name: new RegExp(`add ${skill} to additional skills`, "i"),
        })
      );
    }

    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    // Every preferences save triggers a paid re-extraction (rate-limited to
    // 12/hour). Three saves for three taps would burn a quarter of the user's
    // hourly budget on adding three words.
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        additional_skills: ["Python", "Kubernetes", "Terraform", "Airflow"],
      })
    );
  });

  it("a taken suggestion stops being offered", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={["Kubernetes"]}
        onSave={onSave}
        loading={false}
      />
    );

    const chip = screen.getByRole("button", {
      name: /add kubernetes to additional skills/i,
    });
    fireEvent.click(chip);

    expect(
      screen.queryByRole("button", {
        name: /add kubernetes to additional skills/i,
      })
    ).toBeNull();
  });

  it("never offers a skill the user already listed, whatever the case", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        // The stored skill is "Python"; the extractor proposes "python".
        suggestions={["python", "Kubernetes"]}
        onSave={onSave}
        loading={false}
      />
    );

    expect(
      screen.queryByRole("button", { name: /add python to additional skills/i })
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: /add kubernetes to additional skills/i })
    ).toBeTruthy();
  });

  it("does not save merely because suggestions were rendered", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={["Kubernetes"]}
        onSave={onSave}
        loading={false}
      />
    );
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    // Showing an option is not the user choosing it (rule #29). A save here
    // would also cost a re-extraction on every page load.
    expect(onSave).not.toHaveBeenCalled();
  });

  it("a suggestion chip is not mistaken for a save button", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={initial}
        suggestions={["Kubernetes"]}
        onSave={onSave}
        loading={false}
      />
    );
    // The form's contract is that it has NO manual save control; a chip whose
    // accessible name drifted into "save ..." would quietly break that test's
    // meaning rather than that test.
    expect(
      screen.queryByRole("button", { name: /save preferences/i })
    ).toBeNull();
  });
});

describe("PreferencesForm — words to avoid in job titles", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("is on screen and reaches negative_keywords when used", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm preferences={initial} onSave={onSave} loading={false} />
    );

    const box = screen.getByPlaceholderText(/sales, recruiter/i);
    fireEvent.change(box, { target: { value: "sales" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await act(async () => {
      vi.advanceTimersByTime(900);
    });

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ negative_keywords: ["sales"] })
    );
  });

  it("describes what the penalty actually does", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm preferences={initial} onSave={onSave} loading={false} />
    );
    // The mechanism, verified in skill_matcher.py, is a 30-point deduction on a
    // whole-word TITLE match — not a filter. The old name "Negative Keywords"
    // read like one, which is a promise the code does not keep: the job still
    // appears. If the copy ever drifts back to implying removal, this fails.
    const help = screen.getByText(/drops 30 points/i);
    expect(help.textContent).toMatch(/title/i);
    expect(help.textContent).toMatch(/doesn't hide it/i);
  });
});
