import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BatchQueueCard } from "./BatchQueueCard";

const listApplications = vi.fn();

vi.mock("@/lib/api", () => ({
  listApplications: (...a: unknown[]) => listApplications(...a),
  recordApplicationEvent: vi.fn(),
}));
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }));
const decideOwnBrowserSend = vi.fn();
const getOwnBrowserQueue = vi.fn();
vi.mock("@/lib/browser-contract", () => ({
  decideOwnBrowserSend: (...a: unknown[]) => decideOwnBrowserSend(...a),
  getOwnBrowserQueue: () => getOwnBrowserQueue(),
}));

beforeEach(() => {
  getOwnBrowserQueue.mockReset().mockResolvedValue(null);
  toastSuccess.mockReset();
  toastError.mockReset();
});

const chip = (kind: string, label: string, reason = "default") => ({ kind, label, reason });

function respond(by: Record<string, number>, rows?: unknown[]) {
  listApplications.mockResolvedValue({
    applications: rows ?? [
      { id: 1, job_title: "Greenhouse Job", queue_position: 1, browser: chip("job360", "I'll use Job360's browser") },
      {
        id: 2, job_title: "LinkedIn Job", queue_position: 2,
        browser: chip("own_browser", "Your own browser: runs when your PC is on", "linkedin_own_browser"),
      },
      {
        id: 3, job_title: "Stuck Job", queue_position: 3,
        browser: chip("own_browser", "Needs your own browser: turn your PC on", "job360_login_required"),
      },
    ],
    total: 3,
    queue_summary: { queued: 3, by_browser: by },
  });
}

describe("BatchQueueCard browser chip and filter", () => {
  beforeEach(() => listApplications.mockReset());

  it("shows a chip per job with its text and kind, and the reason in plain words", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    render(<BatchQueueCard />);
    const a = await screen.findByTestId("queue-browser-1");
    expect(a).toHaveTextContent("I'll use Job360's browser");
    expect(a).toHaveAttribute("data-kind", "job360");
    const b = screen.getByTestId("queue-browser-2");
    expect(b).toHaveTextContent("Your own browser: runs when your PC is on");
    expect(b).toHaveAttribute("data-kind", "own_browser");
    expect(screen.getByTestId("queue-browser-why-3")).toHaveTextContent("Job360's browser hit a sign-in wall");
  });

  it("shows the counts from the whole queue and narrows the list", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    render(<BatchQueueCard />);
    await screen.findByTestId("queue-filter");
    expect(screen.getByTestId("queue-filter-all")).toHaveTextContent("All 3");
    expect(screen.getByTestId("queue-filter-job360")).toHaveTextContent("Job360's browser 1");
    expect(screen.getByTestId("queue-filter-own_browser")).toHaveTextContent("Your own browser 2");
    expect(screen.getByTestId("queue-filter-all")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("queue-filter-own_browser"));
    expect(screen.getByTestId("queue-filter-own_browser")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("batch-row-1")).toBeNull();
    expect(screen.getByTestId("batch-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("batch-row-3")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("queue-filter-all"));
    expect(screen.getByTestId("batch-row-1")).toBeInTheDocument();
  });

  it("hides Only you at 0 and shows it above 0", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    const { unmount } = render(<BatchQueueCard />);
    await screen.findByTestId("queue-filter");
    expect(screen.queryByTestId("queue-filter-human")).toBeNull();
    unmount();

    respond({ job360: 1, own_browser: 1, human: 1 });
    render(<BatchQueueCard />);
    expect(await screen.findByTestId("queue-filter-human")).toHaveTextContent("Only you 1");
  });
});

const OWN = chip("own_browser", "Your own browser: runs when your PC is on", "linkedin_own_browser");

function respondMixed() {
  listApplications.mockResolvedValue({
    applications: [
      { id: 1, job_title: "Greenhouse Job", queue_position: 1, browser: chip("job360", "I'll use Job360's browser") },
      { id: 2, job_title: "Second Job", queue_position: 2, browser: chip("job360", "I'll use Job360's browser") },
    ],
    total: 2,
    outreach: [
      {
        task_id: "contact:7", kind: "outreach", outreach_kind: "linkedin_note", title: "LinkedIn note to Priya",
        name: "Priya", contact_id: 7, application_id: 9, message_id: 70, approved_at: "2026-10-26T09:00:00+00:00",
        state: "queued", browser: OWN,
      },
      {
        task_id: "contact:8", kind: "outreach", outreach_kind: "linkedin_message", title: "LinkedIn message to <b>Sam</b>",
        name: "<b>Sam</b>", contact_id: 8, application_id: null, message_id: 80, approved_at: "2026-10-26T11:00:00+00:00",
        state: "queued", browser: OWN,
      },
    ],
    queue_order: ["application:1", "contact:7", "application:2", "contact:8"],
    queue_summary: { queued: 2, outreach: 2, by_browser: { job360: 2, own_browser: 2, human: 0 } },
  });
}

describe("BatchQueueCard one queue with outreach", () => {
  beforeEach(() => {
    listApplications.mockReset();
    decideOwnBrowserSend.mockReset();
  });

  it("renders applications and outreach in the server's order with kind tags and chips", async () => {
    respondMixed();
    render(<BatchQueueCard />);
    const list = await screen.findByTestId("batch-queue-list");
    const ids = Array.from(list.children).map((li) => li.getAttribute("data-testid"));
    expect(ids).toEqual(["batch-row-1", "batch-outreach-7", "batch-row-2", "batch-outreach-8"]);
    expect(screen.getByTestId("batch-kind-application:1")).toHaveTextContent("Application");
    expect(screen.getByTestId("batch-kind-contact:7")).toHaveTextContent("LinkedIn note");
    expect(screen.getByTestId("batch-kind-contact:8")).toHaveTextContent("LinkedIn message");
    expect(screen.getByTestId("queue-browser-contact-7")).toHaveAttribute("data-kind", "own_browser");
    expect(screen.getByTestId("batch-outreach-7")).toHaveTextContent("#2");
    // The person's card: on their job's page, else the People page. Names render as text, never HTML.
    expect(screen.getByText(/LinkedIn note to Priya/).closest("a")).toHaveAttribute("href", "/applications/9#person-7");
    expect(screen.getByText(/LinkedIn message to <b>Sam<\/b>/).closest("a")).toHaveAttribute("href", "/outreach/people");
    expect(list.querySelector("b")).toBeNull();
    expect(screen.getByTestId("queue-filter-all")).toHaveTextContent("All 4");
  });

  it("narrows across both kinds with the browser filter", async () => {
    respondMixed();
    render(<BatchQueueCard />);
    await screen.findByTestId("queue-filter");
    fireEvent.click(screen.getByTestId("queue-filter-own_browser"));
    expect(screen.queryByTestId("batch-row-1")).toBeNull();
    expect(screen.queryByTestId("batch-row-2")).toBeNull();
    expect(screen.getByTestId("batch-outreach-7")).toBeInTheDocument();
    expect(screen.getByTestId("batch-outreach-8")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("queue-filter-job360"));
    expect(screen.queryByTestId("batch-outreach-7")).toBeNull();
    expect(screen.getByTestId("batch-row-1")).toBeInTheDocument();
  });

  it("Don't send declines that one message and reloads", async () => {
    respondMixed();
    decideOwnBrowserSend.mockResolvedValue(undefined);
    render(<BatchQueueCard />);
    fireEvent.click(await screen.findByTestId("batch-dont-send-7"));
    await waitFor(() => expect(decideOwnBrowserSend).toHaveBeenCalledWith(7, 70, "decline"));
    await waitFor(() => expect(listApplications).toHaveBeenCalledTimes(2));
  });
});

// Moved from Needs you (owner 2026-10-27): the own-browser line lives on Home's one queue.
const queue = (over: Record<string, unknown> = {}) => ({
  count: 2, open_now: false, opens_at: null, why: "any_time", tasks: [], pc_on_hours: null, run_now_until: null, ...over,
});
const task = (over: Record<string, unknown>) => ({
  kind: "application", what: "", name: "", application_id: null, contact_id: null, message_id: null, claim: null,
  browser: chip("own_browser", "Needs your own browser: turn your PC on", "job360_login_required"), ...over,
});

describe("BatchQueueCard own-browser tasks", () => {
  beforeEach(() => listApplications.mockReset());

  it("lists every own-browser task once: deduped by application and contact id, the rest merged in", async () => {
    respondMixed(); // applications 1, 2 and people 7, 8 already queued
    getOwnBrowserQueue.mockResolvedValue(queue({
      count: 4,
      tasks: [
        task({ kind: "linkedin_outreach", task_id: "contact:7", contact_id: 7, message_id: 70, name: "Priya" }),
        task({ kind: "linkedin_outreach", task_id: "contact:9", contact_id: 9, message_id: 90, name: "<i>Lee</i>", linkedin_state: "connected" }),
        task({ task_id: "application:2", application_id: 2, what: "Finish the application for Second Job at X" }),
        task({ task_id: "application:5", application_id: 5, what: "Finish the application for <b>Ops</b> at Y" }),
      ],
    }));
    render(<BatchQueueCard />);
    expect(await screen.findByTestId("batch-row-5")).toHaveTextContent("Finish the application for <b>Ops</b> at Y");
    const ids = Array.from(screen.getByTestId("batch-queue-list").children).map((li) => li.getAttribute("data-testid"));
    expect(ids).toEqual(["batch-row-1", "batch-outreach-7", "batch-row-2", "batch-outreach-8", "batch-row-5", "batch-outreach-9"]);
    expect(screen.getByTestId("batch-outreach-9")).toHaveTextContent("LinkedIn message to <i>Lee</i>");
    expect(screen.getByTestId("batch-queue-list").querySelector("b, i")).toBeNull();
    expect(screen.getByTestId("queue-browser-5")).toHaveAttribute("data-kind", "own_browser");
    // A blocked job that was never batch-queued has nothing to remove; its person still has Don't send.
    expect(screen.queryByTestId("batch-remove-5")).toBeNull();
    expect(screen.getByTestId("batch-remove-2")).toBeInTheDocument();
    expect(screen.getByTestId("batch-dont-send-9")).toBeInTheDocument();
    // One number: the filter chip counts 2 + 2 extras.
    expect(screen.getByTestId("queue-filter-own_browser")).toHaveTextContent("Your own browser 4");
    expect(screen.getByTestId("queue-filter-all")).toHaveTextContent("All 6");
  });

  it("re-reads the queue when the assistant moves anything", async () => {
    respond({ job360: 1, own_browser: 2, human: 0 });
    render(<BatchQueueCard />);
    await screen.findByTestId("batch-row-1");
    const before = listApplications.mock.calls.length;
    window.dispatchEvent(new CustomEvent("job360:changes", { detail: { areas: ["applications"], full: false, seq: 1 } }));
    await waitFor(() => expect(listApplications.mock.calls.length).toBe(before + 1));
  });
});
