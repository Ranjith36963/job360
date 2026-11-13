import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Home } from "@/components/home/Home";
import { LAST_VISIT_KEY } from "@/lib/home";

const api = vi.hoisted(() => ({
  whatsNew: vi.fn(),
  getStats: vi.fn(),
  listApplications: vi.fn(),
  listAsks: vi.fn(),
  getNeedsYouCount: vi.fn(),
  answerAsk: vi.fn(),
  withdrawAsk: vi.fn(),
  getAssistantSettings: vi.fn(),
  getReadyToSend: vi.fn(),
  getProfile: vi.fn(),
  uploadProfile: vi.fn(),
  keepProfileEdit: vi.fn(),
  takeBackProfileEdit: vi.fn(),
  getProfileEditHistory: vi.fn(),
  listLessons: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ ...api, ASKS_CHANGED_EVENT: "job360:asks-changed" }));
vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
// The morning check and Batch apply have their own tests; here only where they sit on Home matters.
vi.mock("@/components/needs-you/MorningCheck", () => ({ MorningCheck: () => <div data-testid="morning-check" /> }));
vi.mock("@/components/needs-you/BatchApplyPanel", () => ({ BatchApplyPanel: () => <aside data-testid="batch-apply-panel" /> }));
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

// The one server count (GET /api/needs-you-count) the headline reads.
const serverCount = (by: { you?: number; your_pc?: number; assistant?: number }) => ({
  n: by.you ?? 0, jobs: by.you ?? 0, other: 0, jobs_waiting_on_you: by.you ?? 0,
  by_actor: { you: 0, your_pc: 0, assistant: 0, job360: 0, employer: 0, ...by }, by_kind: {},
});

function happy({ events = [event(1, "agent:Claude"), event(2, "web")], asks = [] as unknown[] } = {}) {
  api.whatsNew.mockResolvedValue(whatsNewOk(events));
  api.getStats.mockResolvedValue({ overall: { brought: 5, applied: 3, replied: 1, interview: 1 } });
  api.listApplications.mockResolvedValue({ applications: [app], total: 1 });
  api.listAsks.mockResolvedValue({ asks, open_count: asks.length });
  api.getNeedsYouCount.mockResolvedValue(serverCount({ you: asks.length }));
  api.getProfile.mockResolvedValue({
    preferences: { assistant_notes: ["Never apply to agencies"] },
    agent_edits: [],
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  api.listLessons.mockResolvedValue({ lessons: [], total: 0 });
});

describe("Home", () => {
  it("shows the sentence and the Activity feed", async () => {
    happy({ asks: [ask(1), ask(2)] });
    render(<Home />);
    const h1 = await screen.findByRole("heading", { level: 1, name: /Your assistant \(Claude\) wrote 1 record/ });
    expect(h1).toHaveTextContent("Your assistant (Claude) wrote 1 record since you were last here. 2 jobs wait on you");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.queryByTestId("home-apps")).toBeNull();
    expect(await screen.findByTestId("home-feed")).toHaveTextContent("Document saved");
    expect(screen.getByRole("heading", { name: "Activity" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Jobs in" })).toHaveAttribute("href", "/bring");
  });

  it("the headline says who the jobs wait on, from the server's by_actor, leaving zero parts out", async () => {
    happy();
    api.getNeedsYouCount.mockResolvedValue(serverCount({ you: 3, your_pc: 2, assistant: 1 }));
    render(<Home />);
    const h1 = await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    expect(h1).toHaveTextContent("3 jobs wait on you · 2 on your PC · 1 on your assistant");
  });

  it("says Nothing waits on you when no job waits on the user", async () => {
    happy();
    api.getNeedsYouCount.mockResolvedValue(serverCount({ you: 0, your_pc: 4 }));
    render(<Home />);
    const h1 = await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    expect(h1).toHaveTextContent("since you were last here. Nothing waits on you");
    expect(h1).not.toHaveTextContent("on your PC");
  });

  it("stores the new last visit only after rendering", async () => {
    happy();
    render(<Home />);
    expect(window.localStorage.getItem(LAST_VISIT_KEY)).toBeNull();
    await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    await waitFor(() => expect(window.localStorage.getItem(LAST_VISIT_KEY)).toBe(NOW));
  });

  it("Home shows no Needs-you cards, even with questions open (owner, 2026-10-26)", async () => {
    happy({ asks: [ask(1), ask(2), ask(3), ask(4)] });
    render(<Home />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("home-asks")).toBeNull();
    expect(screen.queryByTestId("ask-1")).toBeNull();
    expect(screen.queryByRole("link", { name: "See all" })).toBeNull();
  });

  it("the open questions are still counted for the sentence, read once", async () => {
    happy({ asks: [ask(1), ask(2)] });
    render(<Home />);
    await screen.findByRole("heading", { level: 1 });
    expect(api.listAsks).toHaveBeenCalledWith("open");
    expect(screen.queryByTestId("ask-input-1")).toBeNull();
  });

  it("names the last application you confirmed, under the sentence", async () => {
    happy({ events: [event(1, "agent:Claude"), { ...event(2, "agent:Claude"), event_type: "applied" }] });
    render(<Home />);
    const line = await screen.findByTestId("home-subline");
    expect(line.textContent).toMatch(/^The last application you confirmed was Mistral AI, today at .+\.$/);
  });

  it("no applied event: no subline", async () => {
    happy();
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    expect(screen.queryByTestId("home-subline")).toBeNull();
  });

  it("no asks: the section is silent", async () => {
    happy();
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 1 record/ });
    expect(screen.queryByTestId("home-asks")).toBeNull();
    expect(screen.queryByRole("link", { name: "See all" })).toBeNull();
  });

  it("brand-new user: heading and connect steps, no sentence, no ledger, no feed", async () => {
    api.whatsNew.mockResolvedValue(whatsNewOk([]));
    api.getStats.mockResolvedValue({ overall: { brought: 0, applied: 0, replied: 0, interview: 0 } });
    api.listApplications.mockResolvedValue({ applications: [], total: 0 });
    api.listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    render(<Home />);
    expect(await screen.findByTestId("home-welcome")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Progress dashboard" })).toBeInTheDocument();
    expect(screen.getByText(/^Step 1/)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing new since/)).toBeNull();
    expect(screen.queryByTestId("home-feed")).toBeNull();
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

  it("the feed asks for the NEWEST events in one call (newest=true, limit 200)", async () => {
    happy();
    render(<Home />);
    await screen.findByTestId("home-feed");
    expect(api.whatsNew).toHaveBeenCalledTimes(1);
    expect(api.whatsNew.mock.calls[0][0]).toMatchObject({ newest: true, limit: 200 });
    expect(api.whatsNew.mock.calls[0][0].after_id).toBeUndefined();
  });

  // The stop rule: ONE page only when it holds 12 assistant rows AND reaches back to the last visit (7 days
  // ago here) AND holds an `applied` event; otherwise older pages, at most 3.
  const OLD = new Date(Date.now() - 20 * 864e5).toISOString();
  const fresh = (n: number, from = 1) => Array.from({ length: n }, (_, i) => event(from + i, "agent:Claude"));
  const applied = (id: number, recordedAt = NOW) => ({ ...event(id, "agent:Claude"), event_type: "applied", recorded_at: recordedAt });
  const old = (id: number) => ({ ...event(id, "agent:Claude"), recorded_at: OLD });
  const page = (events: unknown[], truncated: boolean) => ({ ...whatsNewOk(events), truncated, next_before_id: 1 });

  it("makes ONE call when the first page has the rows, reaches the last visit and holds an applied event", async () => {
    happy();
    api.whatsNew.mockReset();
    api.whatsNew.mockResolvedValue(page([applied(1, OLD), ...fresh(11, 2)], true));
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 11 records/ });
    expect(api.whatsNew).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent("+");
  });

  it("reads a second page when the first is all newer than the last visit, and then counts exactly", async () => {
    happy();
    api.whatsNew.mockReset();
    api.whatsNew
      .mockResolvedValueOnce(page([applied(20), ...fresh(11, 21)], true))
      .mockResolvedValueOnce(page([old(5)], false));
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 12 records/ });
    expect(api.whatsNew).toHaveBeenCalledTimes(2);
    expect(api.whatsNew.mock.calls[1][0]).toMatchObject({ newest: true, before_id: 1 });
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent("+");
  });

  it("reads a second page when the first holds no applied event, and the applied line shows from it", async () => {
    happy();
    api.whatsNew.mockReset();
    api.whatsNew
      .mockResolvedValueOnce(page([old(30), ...fresh(12, 31)], true))
      .mockResolvedValueOnce(page([applied(5, OLD)], false));
    render(<Home />);
    await screen.findByText(/The last application you confirmed was Mistral AI/);
    expect(api.whatsNew).toHaveBeenCalledTimes(2);
  });

  it("never reads more than 3 pages, and the sentence then says N+", async () => {
    happy();
    api.whatsNew.mockReset();
    let n = 0;
    api.whatsNew.mockImplementation(async () => page(fresh(12, 100 * ++n), true));
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /wrote 36\+ records/ });
    expect(api.whatsNew).toHaveBeenCalledTimes(3);
  });

  it("events from before the last visit are in the feed but not in the sentence", async () => {
    const old = { ...event(3, "agent:Claude"), recorded_at: new Date(Date.now() - 20 * 864e5).toISOString() };
    happy({ events: [old, event(1, "agent:Claude")] });
    render(<Home />);
    await screen.findByRole("heading", { level: 1, name: /Your assistant \(Claude\) wrote 1 record/ });
    expect(screen.getByTestId("home-feed").querySelectorAll("li")).toHaveLength(2);
  });

  it("a failed whats-new call hides Activity; the rest still renders", async () => {
    happy();
    api.whatsNew.mockRejectedValue(new Error("boom"));
    render(<Home />);
    expect(await screen.findByTestId("morning-check")).toBeInTheDocument();
    await waitFor(() => expect(api.whatsNew).toHaveBeenCalled());
    expect(screen.queryByTestId("home-feed")).toBeNull();
    expect(screen.getByTestId("batch-apply-panel")).toBeInTheDocument();
  });

  it("a failed ledger shows an error with a retry", async () => {
    happy();
    api.listApplications.mockRejectedValue(new Error("boom"));
    render(<Home />);
    expect(await screen.findByText("Could not load your applications.")).toBeInTheDocument();
    expect(screen.getByTestId("home-retry")).toBeInTheDocument();
  });

  it("Home carries no assistant-instruction editor (they live on Connect your assistant)", async () => {
    happy();
    render(<Home />);
    await screen.findByTestId("home-sentence");
    expect(screen.queryByTestId("assistant-notes")).toBeNull();
    expect(screen.queryByLabelText("New note for your assistant")).toBeNull();
  });
});

describe("Progress dashboard: now and recent only (owner, 2026-10-28)", () => {
  it("carries no counts and no lessons: they live on Statistics", async () => {
    happy();
    render(<Home />);
    await screen.findByTestId("home-feed");
    expect(screen.queryByTestId("home-counts")).toBeNull();
    expect(screen.queryByText("Your hunt, in counts")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Lessons" })).toBeNull();
    expect(api.getStats).not.toHaveBeenCalled();
    expect(api.listLessons).not.toHaveBeenCalled();
    expect(screen.queryByTestId("home-rail")).toBeNull();
  });

  it("orders header, Right now, Next up, Activity, then the statistics link", async () => {
    happy();
    render(<Home />);
    const sentence = await screen.findByTestId("home-sentence");
    const now = screen.getByRole("heading", { name: "Right now" });
    const morning = screen.getByTestId("morning-check");
    const next = screen.getByRole("heading", { name: "Next up" });
    const batch = screen.getByTestId("batch-apply-panel");
    const activity = await screen.findByRole("heading", { name: "Activity" });
    const link = screen.getByRole("link", { name: /See your statistics/ });
    const order = [sentence, now, morning, next, batch, activity, link];
    const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    order.slice(1).forEach((el, i) => expect(before(order[i], el)).toBe(true));
    expect(link).toHaveAttribute("href", "/stats");
  });

  it("Attention items stay on Needs you and Connect your assistant", async () => {
    happy();
    render(<Home />);
    await screen.findByTestId("home-feed");
    expect(screen.queryByTestId("home-due")).toBeNull();
    expect(screen.queryByTestId("duty-status")).toBeNull();
    expect(screen.queryByTestId("setup-card")).toBeNull();
  });
});
