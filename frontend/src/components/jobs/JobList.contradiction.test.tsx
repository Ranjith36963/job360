/**
 * "No jobs found" must never be shown while the page is holding a count that
 * says jobs exist.
 *
 * MEASURED ON PRODUCTION (job360.uk, real account, 2026-08-24). The dashboard
 * makes two separate requests: one for the list, one for the time-bucket
 * counts. The counts request returned 16,149 bytes in 799ms. The list request
 * took 30,710ms and came back with an empty payload. The page then rendered
 * "No jobs found" and "0 Total Matches" directly beneath its own 7d tab badge
 * reading 100 — while /api/jobs held 226 matching jobs the whole time.
 *
 * To the person looking at it, a job-matching product had just reported that
 * it found them nothing. That is the single most damaging thing this screen
 * can say, and it was false.
 *
 * The list cannot know why its request came back empty, but it CAN know that
 * an empty list disagrees with a non-zero count, and refuse to translate that
 * into "you have no matches".
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobList } from "./JobList";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

describe("JobList — empty list vs known count", () => {
  it("does not claim zero matches when the counts say otherwise", () => {
    render(
      <JobList jobs={[]} loading={false} onAction={vi.fn()} knownAvailable={100} />,
    );

    expect(screen.queryByText("No jobs found")).toBeNull();
    expect(screen.getByText(/couldn't load your matches/i)).toBeInTheDocument();
    // The number the user can see elsewhere on the page is repeated here, so
    // the two halves of the screen agree.
    expect(screen.getByText(/100 jobs match/i)).toBeInTheDocument();
  });

  it("offers a retry when one is available", () => {
    const onRetry = vi.fn();
    render(
      <JobList
        jobs={[]}
        loading={false}
        onAction={vi.fn()}
        knownAvailable={226}
        onRetry={onRetry}
      />,
    );

    const retry = screen.getByRole("button", { name: /try again/i });
    retry.click();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("still says 'No jobs found' when the count genuinely agrees", () => {
    render(
      <JobList jobs={[]} loading={false} onAction={vi.fn()} knownAvailable={0} />,
    );

    expect(screen.getByText("No jobs found")).toBeInTheDocument();
    expect(screen.queryByText(/couldn't load/i)).toBeNull();
  });

  it("shows skeletons, not an empty state, while loading", () => {
    const { container } = render(
      <JobList jobs={[]} loading onAction={vi.fn()} knownAvailable={100} />,
    );

    expect(screen.queryByText(/no jobs found/i)).toBeNull();
    expect(screen.queryByText(/couldn't load/i)).toBeNull();
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });
});
