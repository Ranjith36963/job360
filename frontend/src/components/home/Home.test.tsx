import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { Home } from "./Home";
import { describeEvent } from "./describe-event";

const getWhatsNew = vi.fn();
const getStats = vi.fn();
const listApplications = vi.fn();
const listAsks = vi.fn();
const answerAsk = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getWhatsNew: (...a: unknown[]) => getWhatsNew(...a),
  getStats: (...a: unknown[]) => getStats(...a),
  listApplications: (...a: unknown[]) => listApplications(...a),
  listAsks: (...a: unknown[]) => listAsks(...a),
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  withdrawAsk: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const NOW = new Date().toISOString();
const ago = (mins: number) => new Date(Date.now() - mins * 60_000).toISOString();

let nextId = 1;
function ev(over: Record<string, unknown> = {}) {
  return {
    id: nextId++,
    application_id: 1,
    event_type: "note",
    detail: "",
    payload: {},
    occurred_at: ago(10),
    recorded_at: ago(10),
    recorded_by: "agent:Claude",
    corrects_event_id: null,
    source: null,
    scheduled_at: null,
    ...over,
  };
}

function whatsNew(events: unknown[]) {
  return {
    now: NOW,
    since: ago(7 * 24 * 60),
    events,
    applications: [
      { id: 1, job_title: "AI Engineer", job_company: "Acme", status: "applied", last_event_at: ago(10) },
      { id: 2, job_title: "ML Lead", job_company: "Globex", status: "replied", last_event_at: ago(20) },
    ],
    next_since: NOW,
    next_after_id: null,
    truncated: false,
    open_asks: [],
  };
}

function app(id: number, over: Record<string, unknown> = {}) {
  return {
    id,
    job_id: id,
    job_title: `Role ${id}`,
    job_company: `Company ${id}`,
    job_url: "",
    job_location: "Remote",
    status: "applied",
    last_event_at: ago(30),
    events: 1,
    artifacts: {},
    receipts: 0,
    next_step: { key: "x", label: "" },
    follow_up_on: null,
    follow_up_due: false,
    ...over,
  };
}

function ask(id: number) {
  return {
    id,
    application_id: 1,
    job_title: "AI Engineer",
    job_company: "Acme",
    question: `Question ${id}?`,
    context: "",
    asked_by: "agent:Claude",
    asked_at: ago(5),
    answer: null,
    answered_by: null,
    answered_at: null,
    answered_by_user: false,
    withdrawn_at: null,
    status: "open",
  };
}

const STATS = {
  since: null,
  overall: { brought: 23, applied: 9, replied: 4, interview: 2, offer: 0, rejected: 1 },
  by_cv_version: [],
  by_role: [],
  groups_truncated: false,
  applications_truncated: false,
  computed_at: NOW,
};

function setup({
  events = [] as unknown[],
  asks = [] as unknown[],
  apps = [app(1), app(2, { status: "replied" })] as unknown[],
  total = 23,
  due = [] as unknown[],
} = {}) {
  getWhatsNew.mockResolvedValue(whatsNew(events));
  getStats.mockResolvedValue(STATS);
  listAsks.mockResolvedValue({ asks, open_count: asks.length });
  listApplications.mockImplementation(async (p: { due?: boolean }) =>
    p?.due ? { applications: due, total: due.length } : { applications: apps, total },
  );
}

beforeEach(() => {
  nextId = 1;
  getWhatsNew.mockReset();
  getStats.mockReset();
  listApplications.mockReset();
  listAsks.mockReset();
  answerAsk.mockReset().mockResolvedValue({});
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("Home — the lede", () => {
  it("counts agent-written events only and names the most common assistant", async () => {
    window.localStorage.setItem("job360:home:last-seen", ago(60));
    setup({
      events: [
        ev({ recorded_by: "agent:Claude" }),
        ev({ recorded_by: "agent:Claude" }),
        ev({ recorded_by: "token:laptop" }),
        ev({ recorded_by: "web" }), // the user — not counted
        ev({ recorded_by: "agent:Claude", recorded_at: ago(120) }), // before last visit
      ],
    });
    render(<Home />);
    const lede = await screen.findByTestId("home-lede");
    expect(lede).toHaveTextContent("Claude wrote 3 records since you were last here.");
    expect(lede.tagName).toBe("H1");
    expect(within(lede).getByText("3 records").tagName).toBe("EM");
    // last-seen is written with the server's `now` once the counts are in.
    await waitFor(() => expect(window.localStorage.getItem("job360:home:last-seen")).toBe(NOW));
  });

  it("a capped walk claims no count and does not move last-seen", async () => {
    window.localStorage.setItem("job360:home:last-seen", ago(60));
    getWhatsNew.mockResolvedValue({ ...whatsNew([ev({ recorded_at: ago(9000) })]), truncated: true });
    getStats.mockResolvedValue(STATS);
    listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    listApplications.mockResolvedValue({ applications: [], total: 0 });
    render(<Home />);
    const lede = await screen.findByTestId("home-lede");
    expect(lede).toHaveTextContent("Too many new records to count here");
    expect(lede).not.toHaveTextContent("Nothing new");
    await screen.findByTestId("home-lede");
    expect(window.localStorage.getItem("job360:home:last-seen")).not.toBe(NOW);
  });

  it("a date-only applied_at shows the day without a made-up clock", async () => {
    setup({ events: [ev({ event_type: "applied", occurred_at: "2026-09-02", recorded_by: "web" })] });
    render(<Home />);
    const sub = await screen.findByTestId("home-subline");
    expect(sub).toHaveTextContent(/The last application you confirmed was Acme, on .+\.$/);
    expect(sub).not.toHaveTextContent(/ at \d/);
  });

  it("says 'in the last 7 days' when there is no last visit", async () => {
    setup({ events: [ev()] });
    render(<Home />);
    expect(await screen.findByTestId("home-lede")).toHaveTextContent(
      "Claude wrote 1 record in the last 7 days.",
    );
  });

  it("a refresh in the same session keeps counting from the same last visit", async () => {
    window.localStorage.setItem("job360:home:last-seen", ago(60));
    setup({ events: [ev()] });
    const first = render(<Home />);
    expect(await screen.findByTestId("home-lede")).toHaveTextContent("wrote 1 record");
    first.unmount();
    render(<Home />);
    expect(await screen.findByTestId("home-lede")).toHaveTextContent(
      "Claude wrote 1 record since you were last here.",
    );
  });

  it("zero agent events → the calm 'Nothing new' line", async () => {
    window.localStorage.setItem("job360:home:last-seen", ago(60));
    setup({ events: [ev({ recorded_by: "web" })] });
    render(<Home />);
    expect(await screen.findByTestId("home-lede")).toHaveTextContent(
      /^Nothing new from your assistant since you were last here\.$/,
    );
  });

  it("adds what needs you, and the last confirmed application", async () => {
    setup({
      events: [ev({ event_type: "applied", recorded_by: "web", occurred_at: ago(5) })],
      asks: [ask(1)],
    });
    render(<Home />);
    expect(await screen.findByTestId("home-lede")).toHaveTextContent(/One thing needs you\.$/);
    expect(screen.getByTestId("home-subline")).toHaveTextContent(
      /^The last application you confirmed was Acme, today at .+\.$/,
    );
  });
});

describe("Home — Needs you", () => {
  it("stays silent when nothing is open", async () => {
    setup();
    render(<Home />);
    await screen.findByTestId("home-lede");
    await waitFor(() => expect(listAsks).toHaveBeenCalled());
    expect(screen.queryByTestId("home-needs-you")).toBeNull();
    expect(screen.queryByText(/Needs you/i)).toBeNull();
  });

  it("shows at most 3 cards and a See all link", async () => {
    setup({ asks: [ask(1), ask(2), ask(3), ask(4), ask(5)] });
    render(<Home />);
    const section = await screen.findByTestId("home-needs-you");
    expect(within(section).getAllByRole("listitem")).toHaveLength(3);
    expect(within(section).getByRole("link", { name: "See all (5)" })).toHaveAttribute(
      "href",
      "/needs-you",
    );
    expect(await screen.findByTestId("home-lede")).toHaveTextContent("5 things need you.");
  });

  it("answers through the same confirm flow, then reloads", async () => {
    setup({ asks: [ask(1)] });
    render(<Home />);
    fireEvent.click(await screen.findByTestId("ask-open-1"));
    fireEvent.change(screen.getByTestId("ask-input-1"), { target: { value: "Yes" } });
    fireEvent.click(screen.getByTestId("ask-save"));
    expect(answerAsk).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("ask-answer-confirm"));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(1, "Yes"));
    await waitFor(() => expect(listAsks).toHaveBeenCalledTimes(2));
  });
});

describe("Home — ledger and rail", () => {
  it("renders ledger rows that link to each application", async () => {
    setup({ events: [ev({ application_id: 1, event_type: "artifact_saved", detail: "CV v2" })] });
    render(<Home />);
    const row = await screen.findByTestId("ledger-row-1");
    expect(row).toHaveAttribute("href", "/applications/1");
    expect(screen.getByTestId("ledger-row-2")).toHaveAttribute("href", "/applications/2");
    expect(screen.getByTestId("ledger-count")).toHaveTextContent("2 of 23");
    expect(screen.getByRole("link", { name: "All applications" })).toHaveAttribute(
      "href",
      "/applications",
    );
    expect(screen.getByRole("link", { name: "Bring a job" })).toHaveAttribute("href", "/bring");
    await waitFor(() => expect(row).toHaveTextContent("Saved a document: CV v2"));
  });

  it("shows counts only", async () => {
    setup();
    render(<Home />);
    const counts = await screen.findByTestId("home-counts");
    // <dt> comes first in the DOM (the number sits above it visually).
    expect(counts).toHaveTextContent("Brought23");
    expect(counts).toHaveTextContent("Replies4");
    expect(counts).toHaveTextContent("Interviews2");
    expect(counts).not.toHaveTextContent("%");
  });

  it("hides Due and the feed when they are empty", async () => {
    setup({ events: [ev({ recorded_by: "web" })] });
    render(<Home />);
    await screen.findByTestId("home-lede");
    await waitFor(() => expect(listApplications).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("home-due")).toBeNull();
    expect(screen.queryByTestId("home-feed")).toBeNull();
  });

  it("shows Due rows and the assistant's feed when there is something", async () => {
    setup({
      events: [ev({ application_id: 2, event_type: "replied", detail: "" })],
      due: [app(2, { job_company: "Globex", follow_up_on: "2026-10-03", follow_up_due: true })],
    });
    render(<Home />);
    expect(await screen.findByTestId("home-due")).toHaveTextContent("Follow up with Globex");
    const feed = await screen.findByTestId("home-feed");
    expect(feed).toHaveTextContent("Globex");
    expect(feed).toHaveTextContent("Logged a reply.");
  });

  it("one failing call shows a neutral line; the other sections still render", async () => {
    setup({ events: [ev()] });
    getStats.mockRejectedValue(new Error("boom"));
    render(<Home />);
    expect(await screen.findByTestId("stats-error")).toHaveTextContent(
      "Couldn't load this — refresh to try again.",
    );
    expect(await screen.findByTestId("home-lede")).toHaveTextContent("Claude wrote 1 record");
    expect(await screen.findByTestId("ledger-row-1")).toBeInTheDocument();
  });
});

describe("describeEvent", () => {
  it("maps known types to plain words and humanises unknown ones", () => {
    expect(describeEvent({ event_type: "fit_judged", detail: "" })).toBe("Judged the fit.");
    expect(describeEvent({ event_type: "note", detail: " Called Sam " })).toBe("Added a note: Called Sam");
    expect(describeEvent({ event_type: "salary_discussed", detail: "" })).toBe("Salary discussed.");
  });
});
