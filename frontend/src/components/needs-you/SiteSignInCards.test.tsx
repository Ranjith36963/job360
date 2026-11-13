import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SiteSignInCards } from "./SiteSignInCards";
import { fromServerCount } from "@/lib/needs-you-items";

const signedInOnSite = vi.fn();
vi.mock("@/lib/api", () => ({ signedInOnSite: (...a: unknown[]) => signedInOnSite(...a) }));
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { error: (...a: unknown[]) => toastError(...a) } }));

const hirist = { host: "hirist.tech", word: "sign in", app_ids: [3, 4, 5, 6] };

beforeEach(() => {
  signedInOnSite.mockReset();
  toastError.mockReset();
});

describe("Sign in once per site", () => {
  it("shows one card per site with a link to it, and I've signed in calls the route", async () => {
    signedInOnSite.mockResolvedValue({ host: "hirist.tech", unblocked: [3, 4, 5, 6] });
    const onChanged = vi.fn();
    render(<SiteSignInCards sites={[hirist, { host: "reed.co.uk", word: "create an account", app_ids: [9] }]} onChanged={onChanged} />);
    expect(screen.getByTestId("site-signin-hirist.tech")).toHaveTextContent("Sign in to hirist.tech — unblocks 4 jobs");
    expect(screen.getByTestId("site-signin-reed.co.uk")).toHaveTextContent("Create an account on reed.co.uk — unblocks 1 job");
    expect(screen.getAllByTestId("site-signin-link")[0]).toHaveAttribute("href", "https://hirist.tech");
    fireEvent.click(screen.getAllByTestId("site-signin-done")[0]);
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(signedInOnSite).toHaveBeenCalledWith("hirist.tech");
  });

  it("says so when the save fails, and renders nothing with no sites", async () => {
    signedInOnSite.mockRejectedValue(new Error("down"));
    const onChanged = vi.fn();
    const { unmount } = render(<SiteSignInCards sites={[hirist]} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("site-signin-done"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Could not mark hirist.tech signed in."));
    expect(onChanged).not.toHaveBeenCalled();
    unmount();
    const { container } = render(<SiteSignInCards sites={[]} onChanged={onChanged} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("counts a site once: under All and Sign in / CAPTCHA, and its jobs are not rows", () => {
    const c = fromServerCount({
      n: 2, jobs: 5, other: 0, jobs_waiting_on_you: 5, by_actor: { you: 5 }, by_kind: { site_step: 5 },
      job_ids: [7], job_ids_by_kind: { site_step: [7] }, other_ask_ids: [], answer_once: 0, site_signins: [hirist],
      filters: [
        { key: "all", label: "All", count: 2, job_ids: [], kind: "todo" },
        { key: "signin_captcha", label: "Sign in / CAPTCHA", count: 2, job_ids: [7], kind: "todo" },
      ],
    });
    expect(c.jobIds).toEqual([7]);
    expect(c.byChip.signin_captcha).toBe(2);
    expect(c.byChip.all).toBe(2);
    expect(c.siteSignins).toEqual([hirist]);
  });
});
