import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DueFollowUps } from "./DueFollowUps";
import { StartedNotConfirmed } from "./StartedNotConfirmed";
import { NeedsYou } from "./NeedsYou";

const listApplications = vi.fn();
const recordApplicationReceipt = vi.fn();
const resolveSubmit = vi.fn();
const fetchWhatsNewPages = vi.fn();
const listAsks = vi.fn();

// The one server count (GET /api/needs-you-count) the summary and chips read; tests that check a number set it.
const getNeedsYouCount = vi.hoisted(() => vi.fn());
const ZERO_COUNT = { n: 0, jobs: 0, other: 0, jobs_waiting_on_you: 0, by_actor: {}, by_kind: {} };
vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getNeedsYouCount: (...a: unknown[]) => getNeedsYouCount(...a),
  listApplications: (...a: unknown[]) => listApplications(...a),
  recordApplicationEvent: vi.fn().mockResolvedValue({}),
  recordApplicationReceipt: (...a: unknown[]) => recordApplicationReceipt(...a),
  resolveSubmit: (...a: unknown[]) => resolveSubmit(...a),
  listAsks: (...a: unknown[]) => listAsks(...a),
  getAssistantSettings: async () => ({ waiting: [] }),
  requestJson: async () => ({}),
  getCvToApprove: async () => ({ items: [] }),
  listPeople: async () => ({ people: [] }),
  getReadyToSend: async () => ({ paused: false, total: 0, unflagged: 0, items: [] }),
}));
vi.mock("@/lib/home-fetch", async () => {
  const actual = await vi.importActual<typeof import("@/lib/home-fetch")>("@/lib/home-fetch");
  return { ...actual, fetchWhatsNewPages: (...a: unknown[]) => fetchWhatsNewPages(...a) };
});
vi.mock("./OutcomeSuggestions", () => ({
  useOutcomeSuggestions: () => ({ items: [], busy: false, act: async () => {} }), OutcomeSuggestionCard: () => null,
}));
vi.mock("./UnmatchedMail", () => ({
  useUnmatchedMail: () => ({ items: [], apps: [], resolve: async () => {} }), InboxItemCard: () => null,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ev = (id: number, application_id: number, event_type: string) => ({
  id, application_id, event_type, occurred_at: "2026-10-17T10:00:00Z", payload: {}, corrects_event_id: null,
});
const app = { id: 7, job_title: "Engineer", job_company: "Mistral AI", follow_up_due: false };

beforeEach(() => {
  getNeedsYouCount.mockReset().mockResolvedValue(ZERO_COUNT);
  listApplications.mockReset().mockResolvedValue({ applications: [], total: 0 });
  recordApplicationReceipt.mockReset().mockResolvedValue({});
  resolveSubmit.mockReset().mockResolvedValue({});
  listAsks.mockReset().mockResolvedValue({ asks: [], open_count: 0 });
  fetchWhatsNewPages
    .mockReset()
    .mockResolvedValue({ events: [ev(1, 5, "submit_attempted")], applications: [{ id: 5, job_title: "Analyst", job_company: "Gamma" }] });
});

describe("Needs you: Started, not confirmed (moved from Home)", () => {
  it("'It was sent' records the application after a confirm, then refreshes", async () => {
    render(<StartedNotConfirmed />);
    fireEvent.click(await screen.findByTestId("submit-was-sent-5"));
    fireEvent.click(screen.getByTestId("submit-sent-confirm-5"));
    await waitFor(() => expect(recordApplicationReceipt).toHaveBeenCalledWith(5, {}));
    await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalledTimes(2));
  });

  it("'It was not sent' closes the stop, then refreshes", async () => {
    render(<StartedNotConfirmed />);
    fireEvent.click(await screen.findByTestId("submit-not-sent-5"));
    await waitFor(() => expect(resolveSubmit).toHaveBeenCalledWith(5));
    await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalledTimes(2));
  });

  it("is hidden when nothing is waiting", async () => {
    fetchWhatsNewPages.mockResolvedValue({ events: [], applications: [] });
    render(<StartedNotConfirmed />);
    await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalled());
    expect(screen.queryByTestId("started-not-confirmed")).toBeNull();
  });
});

describe("Needs you: due follow-ups (moved from Home)", () => {
  it("due follow-ups come from the ledger rows, soonest first", async () => {
    listApplications.mockResolvedValue({
      applications: [
        { ...app, id: 8, job_company: "Lakera", follow_up_due: true, follow_up_on: "2026-10-09" },
        { ...app, id: 9, job_company: "Sana", follow_up_due: true, follow_up_on: "2026-10-05" },
        app,
      ],
      total: 3,
    });
    render(<DueFollowUps />);
    const due = await screen.findByTestId("home-due");
    const text = due.textContent ?? "";
    expect(text.indexOf("Sana")).toBeGreaterThan(-1);
    expect(text.indexOf("Sana")).toBeLessThan(text.indexOf("Lakera"));
    expect(text).not.toContain("Mistral AI");
  });

  it("is silent when nothing is due or the read fails", async () => {
    listApplications.mockRejectedValue(new Error("down"));
    const { container } = render(<DueFollowUps />);
    await waitFor(() => expect(listApplications).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("both sit on the Needs you page", async () => {
    listApplications.mockResolvedValue({
      applications: [{ ...app, id: 8, follow_up_due: true, follow_up_on: "2026-10-09" }],
      total: 1,
    });
    render(<NeedsYou />);
    const main = await screen.findByTestId("needs-you-main");
    expect(main).toContainElement(await screen.findByTestId("started-not-confirmed"));
    expect(main).toContainElement(await screen.findByTestId("home-due"));
  });
});
