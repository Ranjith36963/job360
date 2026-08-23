/**
 * The dashboard actually WIRES the search titles to the screen.
 *
 * The component test next door proves `SearchingFor` renders correctly when
 * handed titles. This proves the dashboard hands it the REAL ones — i.e. that
 * `GET /api/profile` is called and its `search_titles` reach the DOM. Without
 * this, a refactor could drop the query and the component test would stay
 * green while the user saw nothing (the exact failure mode this whole batch
 * exists to fix: signal that is computed, stored, and shown to nobody).
 *
 * The second test pins the degrade path at PAGE level: a user with no profile
 * gets a 404 from that endpoint, and the dashboard must carry on as if the
 * line did not exist — no banner, no empty label, jobs still rendered.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import DashboardPage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));

const getJobsMock = vi.fn();
const getProfileMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJobs: (...args: unknown[]) => getJobsMock(...args),
  getProfile: (...args: unknown[]) => getProfileMock(...args),
  getStatus: vi.fn().mockResolvedValue({
    jobs_total: 0,
    last_run: null,
    sources_active: 0,
    sources_total: 0,
    profile_exists: true,
  }),
}));

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

describe("DashboardPage — shows what we searched for", () => {
  beforeEach(() => {
    getJobsMock.mockReset();
    getProfileMock.mockReset();
    getJobsMock.mockResolvedValue({ jobs: [], total: 0, filters_applied: {} });
    sessionStorage.clear();
  });

  it("renders the search titles the API reports", async () => {
    getProfileMock.mockResolvedValue({
      search_titles: ["AI Engineer", "ML Engineer"],
    });

    render(<DashboardPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(getProfileMock).toHaveBeenCalled());
    const line = await screen.findByTestId("searching-for");
    expect(line).toHaveTextContent("AI Engineer, ML Engineer");
    expect(screen.getByRole("link", { name: /change/i })).toHaveAttribute(
      "href",
      "/profile#target-job-titles"
    );
  });

  it("shows nothing at all when the profile call fails (404 = new account)", async () => {
    getProfileMock.mockRejectedValue(new Error("404 No profile found"));

    render(<DashboardPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(getJobsMock).toHaveBeenCalled());
    await waitFor(() => expect(getProfileMock).toHaveBeenCalled());

    expect(screen.queryByTestId("searching-for")).toBeNull();
    expect(screen.queryByText(/searching for/i)).toBeNull();
    // The page itself is unharmed — the dashboard heading still rendered.
    expect(screen.getByRole("heading", { name: /dashboard/i })).toBeInTheDocument();
  });
});
