import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Home } from "@/components/home/Home";
import { LAST_VISIT_KEY } from "@/lib/home";

const api = vi.hoisted(() => ({
  whatsNew: vi.fn(),
  getStats: vi.fn(),
  listApplications: vi.fn(),
  listAsks: vi.fn(),
  answerAsk: vi.fn(),
  withdrawAsk: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ ...api, ASKS_CHANGED_EVENT: "job360:asks-changed" }));
vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// "Now" is the real clock: the sentence counts events after the last visit
// (7 days ago when none is stored), so a fixed date would rot.
const NOW = new Date().toISOString();

const event = (id: number, by: string) => ({
  id,
  application_id: 7,
  event_type: "artifact_saved",
  detail: "",
  payload: {},
  occurred_at: NOW,
  recorded_at: NOW,
  recorded_by: by,
  corrects_event_id: null,
  scheduled_at: null,
  source: null,
});

const whatsNewOk = (events: unknown[]) => ({
  now: NOW,
  since: NOW,
  events,
  applications: [{ id: 7, job_title: "Engineer", job_company: "Mistral AI", last_event_at: NOW, status: "applied" }],
  next_since: NOW,
  next_after_id: null,
  truncated: false,
  open_asks: [],
});

const app = {
  id: 7,
  job_id: 1,
  job_title: "Engineer",
  job_company: "Mistral AI",
  job_url: "",
  job_location: "",
  status: "applied",
  last_event_at: NOW,
  events: 2,
  artifacts: {},
  receipts: 0,
  follow_up_due: false,
  next_step: { code: "wait", label: "Waiting" },
  fit_score: 70,
  fit_verdict: "Good fit",
};

const ask = (id: number) => ({
  id,
  question: `Question ${id}?`,
  context: "",
  asked_by: "Claude",
  asked_at: NOW,
  status: "open",
  answer: null,
  answered_by_user: false,
  application_id: 7,
  job_title: "Engineer",
  job_company: "Mistral AI",
});

function happy({ events = [event(1, "agent:Claude"), event(2, "web")], asks = [] as unknown[] } = {}) {
  api.whatsNew.mockResolvedValue(whatsNewOk(events));
  api.getStats.mockResolvedValue({ overall: { brought: 5, applied: 3, replied: 1, interview: 1 } });
  api.listApplications.mockResolvedValue({ applications: [app], total: 1 });
  api.listAsks.mockResolvedValue({ asks, open_count: asks.length });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("Home", () => {
  it("shows the sentence, the ledger and the right pane", async () => {
    happy({ asks: [ask(1), ask(2)] });
    render(<Home />);
    const h1 = await screen.findByRole("heading", { level: 1, name: /Claude wrote 1 record/ });
    expect(h1).toHaveTextContent("Claude wrote 1 record since you were last here. Two things need you.");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(await screen.findByTestId("home-apps")).toHaveTextContent("Mistral AI");
    expect(screen.getByTestId("row-fit")).toHaveTextContent("Good fit");
    expect(await screen.findByTestId("home-counts")).toHaveTextContent("jobs brought");
    expect(screen.getByTestId("home-feed")).toHaveTextContent("Document saved");
    expect(screen.getByRole("link", { name: "Jobs in" })).toHaveAttribute("href", "/bring");
  });

  it("stores the new last visit only after rendering", async () => {
    happy();
    render(<Home />);
    expect(window.localStorage.getItem(LAST_VISIT_KEY)).toBeNull();
    await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    await waitFor(() => expect(window.localStorage.getItem(LAST_VISIT_KEY)).toBe(NOW));
  });

  it("first 3 asks as cards, with See all when there are more", async () => {
    happy({ asks: [ask(1), ask(2), ask(3), ask(4)] });
    render(<Home />);
    expect(await screen.findByTestId("ask-1")).toBeInTheDocument();
    expect(screen.getByTestId("ask-3")).toBeInTheDocument();
    expect(screen.queryByTestId("ask-4")).toBeNull();
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute("href", "/needs-you");
  });

  it("no asks: the section is silent", async () => {
    happy();
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    expect(screen.queryByTestId("home-asks")).toBeNull();
    expect(screen.queryByRole("link", { name: "See all" })).toBeNull();
  });

  it("brand-new user: heading and connect steps, no sentence, no ledger, no rail", async () => {
    api.whatsNew.mockResolvedValue(whatsNewOk([]));
    api.getStats.mockResolvedValue({ overall: { brought: 0, applied: 0, replied: 0, interview: 0 } });
    api.listApplications.mockResolvedValue({ applications: [], total: 0 });
    api.listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    render(<Home />);
    expect(await screen.findByTestId("home-welcome")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Home" })).toBeInTheDocument();
    expect(screen.getByText(/^Step 1/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing new since/)).toBeNull();
    expect(screen.queryByTestId("home-rail")).toBeNull();
    expect(screen.queryByTestId("home-apps")).toBeNull();
  });

  it("asks the backend as little as it can: one whats-new read, no separate due call", async () => {
    happy();
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    await screen.findByTestId("home-feed");
    expect(api.whatsNew).toHaveBeenCalledTimes(1);
    expect(api.listApplications).toHaveBeenCalledTimes(1);
    expect(api.listApplications.mock.calls[0][0]).not.toHaveProperty("due");
  });

  it("due follow-ups come from the ledger rows, soonest first", async () => {
    happy();
    api.listApplications.mockResolvedValue({
      applications: [
        { ...app, id: 8, job_company: "Lakera", follow_up_due: true, follow_up_on: "2026-10-09" },
        { ...app, id: 9, job_company: "Sana", follow_up_due: true, follow_up_on: "2026-10-05" },
        app,
      ],
      total: 3,
    });
    render(<Home />);
    const due = await screen.findByTestId("home-due");
    const text = due.textContent ?? "";
    expect(text.indexOf("Sana")).toBeGreaterThan(-1);
    expect(text.indexOf("Sana")).toBeLessThan(text.indexOf("Lakera"));
    expect(text).not.toContain("Mistral AI");
  });

  it("events from before the last visit are in the feed but not in the sentence", async () => {
    const old = { ...event(3, "agent:Claude"), recorded_at: new Date(Date.now() - 20 * 864e5).toISOString() };
    happy({ events: [old, event(1, "agent:Claude")] });
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /Claude wrote 1 record/ });
    expect(screen.getByTestId("home-feed").querySelectorAll("li")).toHaveLength(2);
  });

  it("a failed right-pane call shows nothing; the rest still renders", async () => {
    happy();
    api.getStats.mockRejectedValue(new Error("boom"));
    render(<Home />);
    expect(await screen.findByTestId("home-apps")).toBeInTheDocument();
    expect(screen.queryByTestId("home-counts")).toBeNull();
    expect(screen.getByTestId("home-feed")).toBeInTheDocument();
  });

  it("a failed ledger shows an error with a retry", async () => {
    happy();
    api.listApplications.mockRejectedValue(new Error("boom"));
    render(<Home />);
    expect(await screen.findByText("Could not load your applications.")).toBeInTheDocument();
    expect(screen.getByTestId("home-retry")).toBeInTheDocument();
  });
});
