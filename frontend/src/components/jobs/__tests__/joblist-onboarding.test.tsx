/**
 * W-03 — day one must not be a dead end.
 *
 * A brand-new account has no profile and no jobs. It used to land on the dashboard
 * and read: "Try adjusting your filters, expanding the time range, or lowering the
 * minimum score." Every word of that is wrong for a new user — there are no filters
 * set, no search has ever run, and there is no score to lower. The real reason the
 * screen is empty is that no CV has been uploaded, and nothing said so or linked
 * anywhere (wiring.md W-03).
 *
 * The regression that matters just as much: a user who DOES have a profile and
 * genuinely got zero results must still get the old filter advice. The new message
 * must not swallow the real empty state.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { JobList } from "../JobList";
import type { JobResponse } from "@/lib/types";

// JobCard pulls in the router, posthog and toasts — none of which this test is
// about. Stub it so the test only exercises JobList's own branching.
vi.mock("@/components/jobs/JobCard", () => ({
  JobCard: ({ job }: { job: JobResponse }) => <div data-testid="job-card">{job.title}</div>,
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const FILTER_ADVICE = /adjusting your filters/i;

function renderList(props: Partial<React.ComponentProps<typeof JobList>> = {}) {
  return render(
    <JobList jobs={[]} loading={false} onAction={vi.fn()} {...props} />
  );
}

describe("JobList — the first screen a new account sees", () => {
  it("points a profile-less user at their CV, not at filters they never set", () => {
    renderList({ hasProfile: false });

    // The wrong advice must be gone...
    expect(screen.queryByText(FILTER_ADVICE)).toBeNull();
    // ...and there must be a real way forward.
    const cta = screen.getByRole("link", { name: /cv|profile|get started/i });
    expect(cta).toHaveAttribute("href", "/profile");
  });

  it("still shows the filter advice to someone who HAS a profile and got zero results", () => {
    renderList({ hasProfile: true });

    expect(screen.getByText(FILTER_ADVICE)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /cv|profile|get started/i })).toBeNull();
  });

  it("does not flash the onboarding message while the profile is still loading", () => {
    // `undefined` means "we do not know yet". Showing the CV prompt here would
    // blink a wrong message at every returning user on every page load.
    renderList({ hasProfile: undefined });

    expect(screen.getByText(FILTER_ADVICE)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /cv|profile|get started/i })).toBeNull();
  });

  it("shows no empty state at all once there are jobs", () => {
    const job = { id: 1, title: "Platform Engineer" } as JobResponse;
    renderList({ jobs: [job], hasProfile: false });

    expect(screen.getByTestId("job-card")).toBeTruthy();
    expect(screen.queryByText(FILTER_ADVICE)).toBeNull();
    expect(screen.queryByRole("link", { name: /cv|profile|get started/i })).toBeNull();
  });

  it("shows skeletons, not any empty state, while jobs are loading", () => {
    renderList({ loading: true, hasProfile: false });

    expect(screen.queryByText(FILTER_ADVICE)).toBeNull();
    expect(screen.queryByRole("link", { name: /cv|profile|get started/i })).toBeNull();
  });
});
