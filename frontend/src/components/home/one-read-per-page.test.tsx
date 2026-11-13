import { beforeEach, describe, expect, it, vi } from "vitest";
import { useEffect, useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { Home } from "@/components/home/Home";
import { BatchQueueCard } from "@/components/needs-you/BatchQueueCard";
import { useOpenAsks } from "@/components/layout/useOpenAsks";
import { CHANGES_EVENT } from "@/lib/live-changes";

// One page load = ONE read of each shared endpoint (prod trace 2026-11-13: Home made 5 applications pages
// and 3 needs-you-count calls). The badge, Home and the batch card share one count read, and Home needs
// only `total` of the applications list.

const api = vi.hoisted(() => ({
  whatsNew: vi.fn(),
  listApplications: vi.fn(),
  listAsks: vi.fn(),
  getNeedsYouCount: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ ...api, ASKS_CHANGED_EVENT: "job360:asks-changed" }));
// A new "page" per test: the shared readers keep an answer per key for a few seconds.
const nav = vi.hoisted(() => ({ path: "/", n: 0 }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path }));
vi.mock("@/lib/browser-contract", () => ({ getOwnBrowserQueue: vi.fn().mockResolvedValue(null), decideOwnBrowserSend: vi.fn() }));
vi.mock("@/components/needs-you/BatchApplyPanel", () => ({ BatchApplyPanel: () => <BatchQueueCard /> }));
vi.mock("@/components/needs-you/MorningCheck", () => ({ MorningCheck: () => null }));
vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const NOW = new Date().toISOString();
const count = (n: number) => ({
  n, jobs: n, other: 0, jobs_waiting_on_you: n,
  by_actor: { you: n, your_pc: 0, assistant: 0, job360: 0, employer: 0 }, by_kind: {},
});
const summary = { queued: 1, ready_to_send: 0, asked: 0, blocked: 0, outreach: 0 };

function Badge() {
  return <span data-testid="badge">{useOpenAsks(true, nav.path)}</span>;
}

beforeEach(() => {
  nav.path = `/page-${++nav.n}`;
  vi.clearAllMocks();
  window.localStorage.clear();
  api.whatsNew.mockResolvedValue({
    now: NOW, since: NOW, events: [], applications: [], next_since: NOW, next_after_id: null, truncated: false, open_asks: [],
  });
  api.listApplications.mockImplementation(async (a?: { queued?: boolean }) =>
    a?.queued
      ? { applications: [{ id: 11, queue_position: 1, job_title: "Analyst", job_company: "Acme", preflight: {} }], total: 1, queue_summary: summary }
      : { applications: [], total: 450 },
  );
  api.listAsks.mockResolvedValue({ asks: [], open_count: 0 });
  api.getNeedsYouCount.mockResolvedValue(count(3));
});

describe("one read per page", () => {
  it("Home reads the applications list once, with limit 1, and still gets the total", async () => {
    render(<Home />);
    await waitFor(() => expect(screen.getByTestId("home-sentence")).toHaveTextContent(/wait on you/));
    const plain = api.listApplications.mock.calls.map((c) => c[0]).filter((a) => !a?.queued);
    expect(plain).toEqual([{ limit: 1 }]);
  });

  it("the badge, Home and the batch card make ONE needs-you-count call, and one more after a live change", async () => {
    render(
      <>
        <Badge />
        <Home />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("badge")).toHaveTextContent("3"));
    await waitFor(() => expect(screen.getByTestId("home-sentence")).toHaveTextContent("3 jobs wait on you"));
    await waitFor(() => expect(screen.getByTestId("batch-queue-summary")).toHaveTextContent("3 need you"));
    expect(api.getNeedsYouCount).toHaveBeenCalledTimes(1);

    api.getNeedsYouCount.mockResolvedValue(count(5));
    act(() => {
      window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { areas: ["asks"], full: false, seq: 41 } }));
    });
    await waitFor(() => expect(screen.getByTestId("badge")).toHaveTextContent("5"));
    await waitFor(() => expect(screen.getByTestId("home-sentence")).toHaveTextContent("5 jobs wait on you"));
    await waitFor(() => expect(screen.getByTestId("batch-queue-summary")).toHaveTextContent("5 need you"));
    expect(api.getNeedsYouCount).toHaveBeenCalledTimes(2);
  });

  it("a slow page-load count that arrives after a live re-read does not overwrite the newer number", async () => {
    let answerFirst!: (c: ReturnType<typeof count>) => void;
    api.getNeedsYouCount.mockReturnValueOnce(new Promise((r) => (answerFirst = r)));
    render(<Home />);
    // The live change comes while the page-load read is still out: a second, newer read.
    api.getNeedsYouCount.mockResolvedValue(count(5));
    act(() => {
      window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { areas: ["asks"], full: false, seq: 42 } }));
    });
    await waitFor(() => expect(screen.getByTestId("home-sentence")).toHaveTextContent("5 jobs wait on you"));
    await act(async () => {
      answerFirst(count(3)); // the older answer, late
    });
    expect(screen.getByTestId("home-sentence")).toHaveTextContent("5 jobs wait on you");
  });

  it("a consumer that mounts late (the badge waits for the signed-in user) shares the settled read", async () => {
    function LateBadge() {
      const [on, setOn] = useState(false);
      useEffect(() => {
        const t = setTimeout(() => setOn(true), 300); // after Home's read has settled
        return () => clearTimeout(t);
      }, []);
      return on ? <Badge /> : null;
    }
    render(
      <>
        <Home />
        <LateBadge />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("home-sentence")).toHaveTextContent("3 jobs wait on you"));
    expect(api.getNeedsYouCount).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId("badge")).toHaveTextContent("3"));
    expect(api.getNeedsYouCount).toHaveBeenCalledTimes(1);
  });
});
