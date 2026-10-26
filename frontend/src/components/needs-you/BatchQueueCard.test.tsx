import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BatchQueueCard } from "./BatchQueueCard";

const listApplications = vi.fn();

vi.mock("@/lib/api", () => ({
  listApplications: (...a: unknown[]) => listApplications(...a),
  recordApplicationEvent: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const decideOwnBrowserSend = vi.fn();
vi.mock("@/lib/browser-contract", () => ({
  decideOwnBrowserSend: (...a: unknown[]) => decideOwnBrowserSend(...a),
}));

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
