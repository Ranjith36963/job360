/**
 * Dashboard uses all three matching engines.
 *
 * "Use all three" resolves on the wire to a single request flag: the dashboard
 * must ask /api/jobs for `mode=hybrid`. That one parameter is what turns on the
 * semantic re-rank (engine #3) on top of the keyword score (#1) and the
 * enrichment dims already folded into that score (#2). Without it the route
 * serves keyword-only order and semantic never fires — silently.
 *
 * This pins the contract so a future filter refactor can't drop the flag
 * unnoticed. The end-to-end proof (semantic actually reorders against the
 * user's profile) is the Playwright/real-app drive; this is the cheap CI guard.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import DashboardPage from "../page";

const getJobsMock = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJobs: (...args: unknown[]) => getJobsMock(...args),
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

describe("DashboardPage — uses all three engines via mode=hybrid", () => {
  beforeEach(() => {
    getJobsMock.mockReset();
    getJobsMock.mockResolvedValue({ jobs: [], total: 0, filters_applied: {} });
    sessionStorage.clear();
  });

  it("requests the main job list with mode='hybrid'", async () => {
    render(<DashboardPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(getJobsMock).toHaveBeenCalled());

    const usedHybrid = getJobsMock.mock.calls.some(
      ([filters]) => filters && (filters as { mode?: string }).mode === "hybrid"
    );
    expect(usedHybrid).toBe(true);
  });
});
