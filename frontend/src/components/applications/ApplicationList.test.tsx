import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApplicationList } from "./ApplicationList";
import type { ApplicationSummary } from "@/lib/api";
import { formatDayMonth } from "@/lib/format-date";

const listApplications = vi.fn();

vi.mock("@/lib/api", () => ({
  listApplications: (...args: unknown[]) => listApplications(...args),
}));

function summary(overrides: Partial<ApplicationSummary> = {}): ApplicationSummary {
  return {
    id: 1,
    job_id: 100,
    job_title: "Staff Engineer",
    job_company: "Acme",
    job_url: "https://jobs.lever.co/acme/1",
    status: "considering",
    last_event_at: "2026-09-20T00:00:00Z",
    events: 1,
    artifacts: {},
    receipts: 0,
    visa_signal: "unknown",
    visa_country: "",
    needs_sponsorship: null,
    next_step: { code: "judge_fit", label: "No fit judged yet — ask your assistant to judge it" },
    follow_up_on: null,
    follow_up_due: false,
    ...overrides,
  };
}

describe("ApplicationList — Due filter (owner decision, 2026-09-25)", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("shows no Due chip when nothing is due", async () => {
    listApplications.mockResolvedValue({ applications: [summary()], total: 1 });
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    expect(screen.queryByTestId("due-filter")).toBeNull();
  });

  it("shows a Due chip with a count and an amber tag on the due row", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, job_title: "Due Job", follow_up_on: "2026-10-03", follow_up_due: true }),
        summary({ id: 2, job_title: "Not Due Job", follow_up_due: false }),
      ],
      total: 2,
    });
    render(<ApplicationList />);
    await screen.findByText("Due Job");

    expect(screen.getByTestId("due-filter")).toHaveTextContent("Due (1)");
    // Dates follow the viewer's locale ("3 Oct" in the UK, "Oct 3" on a US
    // CI runner), so compare against the formatter rather than one order.
    expect(screen.getByTestId("row-follow-up")).toHaveTextContent(
      `Follow up ${formatDayMonth("2026-10-03")}`,
    );
  });

  it("filters to only due rows when the Due chip is clicked", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, job_title: "Due Job", follow_up_on: "2026-10-03", follow_up_due: true }),
        summary({ id: 2, job_title: "Not Due Job", follow_up_due: false }),
      ],
      total: 2,
    });
    render(<ApplicationList />);
    await screen.findByText("Due Job");
    expect(screen.getByText("Not Due Job")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("due-filter"));

    await waitFor(() => expect(screen.queryByText("Not Due Job")).toBeNull());
    expect(screen.getByText("Due Job")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("due-filter"));
    await waitFor(() => expect(screen.getByText("Not Due Job")).toBeInTheDocument());
  });
});

describe("ApplicationList — Mark Applied is gone from the row", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("never renders a Mark Applied button on any row", async () => {
    listApplications.mockResolvedValue({ applications: [summary()], total: 1 });
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");

    expect(screen.queryByRole("button", { name: /mark applied/i })).toBeNull();
  });
});

describe("ApplicationList — the Next line replaces the counts line", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("shows the next_step label instead of an events/documents/receipts count", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({
          events: 3,
          artifacts: { cv: 2 },
          receipts: 1,
          next_step: { code: "apply", label: "CV ready — apply, then mark it applied" },
        }),
      ],
      total: 1,
    });
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");

    expect(screen.getByTestId("row-next-step")).toHaveTextContent(
      "Next: CV ready — apply, then mark it applied"
    );
    expect(screen.queryByText(/3 events?/i)).toBeNull();
    expect(screen.queryByText(/2 documents?/i)).toBeNull();
    expect(screen.queryByText(/1 receipts?/i)).toBeNull();
  });
});

describe("ApplicationList — title fallback", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("uses the job title when present", async () => {
    listApplications.mockResolvedValue({
      applications: [summary({ job_title: "Staff Engineer", job_company: "Acme" })],
      total: 1,
    });
    render(<ApplicationList />);
    expect(await screen.findByText("Staff Engineer")).toBeInTheDocument();
  });

  it("falls back to the company when the title is empty", async () => {
    listApplications.mockResolvedValue({
      applications: [summary({ job_title: "", job_company: "Acme" })],
      total: 1,
    });
    render(<ApplicationList />);
    // Both the title slot and the company line read "Acme" (the title fell
    // back to the company) — two matches, not zero or one.
    expect(await screen.findAllByText("Acme")).toHaveLength(2);
  });

  it("falls back to the ad link's host when both title and company are empty", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ job_title: "", job_company: "", job_url: "https://jobs.lever.co/acme/1" }),
      ],
      total: 1,
    });
    render(<ApplicationList />);
    expect(await screen.findByText("jobs.lever.co")).toBeInTheDocument();
  });

  it('falls back to "Untitled job" when title, company and URL are all empty', async () => {
    listApplications.mockResolvedValue({
      applications: [summary({ job_title: "", job_company: "", job_url: "" })],
      total: 1,
    });
    render(<ApplicationList />);
    expect(await screen.findByText("Untitled job")).toBeInTheDocument();
  });
});

describe("ApplicationList — long titles wrap instead of being cut to one line (owner report, 2026-09-25)", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("renders a long title with line-clamp-2 and the full text in a title attribute, not truncate", async () => {
    const longTitle = "AI Engineer (LLMs, Agents & Applied Machine Learning Platform)";
    const longCompany = "Some Very Long Company Name Holdings International Limited";
    listApplications.mockResolvedValue({
      applications: [summary({ job_title: longTitle, job_company: longCompany })],
      total: 1,
    });
    render(<ApplicationList />);

    const titleEl = await screen.findByText(longTitle);
    expect(titleEl).toHaveClass("line-clamp-2");
    expect(titleEl).not.toHaveClass("truncate");
    expect(titleEl).toHaveAttribute("title", longTitle);

    const companyEl = screen.getByText(longCompany);
    expect(companyEl).toHaveClass("line-clamp-2");
    expect(companyEl).not.toHaveClass("truncate");
    expect(companyEl).toHaveAttribute("title", longCompany);
  });
});

describe("ApplicationList — status filter", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("shows an All chip plus one chip per status present, with counts", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, job_title: "Job One", status: "considering" }),
        summary({ id: 2, job_title: "Job Two", status: "considering" }),
        summary({ id: 3, job_title: "Job Three", status: "applied" }),
      ],
      total: 3,
    });
    render(<ApplicationList />);
    await screen.findByText("Job One");

    const filter = screen.getByTestId("status-filter");
    expect(filter).toHaveTextContent("All (3)");
    expect(filter).toHaveTextContent("Considering (2)");
    expect(filter).toHaveTextContent("Applied (1)");
  });

  it("filters the visible rows when a status chip is clicked", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, job_title: "Job One", status: "considering" }),
        summary({ id: 2, job_title: "Job Two", status: "applied" }),
      ],
      total: 2,
    });
    render(<ApplicationList />);
    await screen.findByText("Job One");
    expect(screen.getByText("Job Two")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^applied \(1\)$/i }));

    await waitFor(() => expect(screen.queryByText("Job One")).toBeNull());
    expect(screen.getByText("Job Two")).toBeInTheDocument();
  });

  it("returns to showing every row when All is clicked again", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, job_title: "Job One", status: "considering" }),
        summary({ id: 2, job_title: "Job Two", status: "applied" }),
      ],
      total: 2,
    });
    render(<ApplicationList />);
    await screen.findByText("Job One");

    fireEvent.click(screen.getByRole("button", { name: /^applied \(1\)$/i }));
    await waitFor(() => expect(screen.queryByText("Job One")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: /^all \(2\)$/i }));
    await waitFor(() => expect(screen.getByText("Job One")).toBeInTheDocument());
    expect(screen.getByText("Job Two")).toBeInTheDocument();
  });
});

describe("ApplicationList — fit display (redesign slice 2)", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("shows the assistant's verdict as the main text and the score as a small 70/100", async () => {
    listApplications.mockResolvedValue({
      applications: [summary({ fit_score: 70, fit_verdict: "Good fit for agent work" })],
      total: 1,
    });
    render(<ApplicationList />);
    const fit = await screen.findByTestId("row-fit");
    expect(fit).toHaveTextContent("Good fit for agent work");
    expect(fit).toHaveTextContent("70/100");
    expect(fit.querySelector(".bg-primary")).toBeNull();
  });

  it("with no fit judged, row-fit is absent and the next-step line stays", async () => {
    listApplications.mockResolvedValue({ applications: [summary()], total: 1 });
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    expect(screen.queryByTestId("row-fit")).toBeNull();
    expect(screen.getByTestId("row-next-step")).toHaveTextContent("No fit judged yet");
  });

  it("labels the score against the user's own line; unknown shows nothing", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, fit_score: 80, line_side: "above" } as never),
        summary({ id: 2, fit_score: 40, line_side: "below" } as never),
        summary({ id: 3, fit_score: 55, line_side: "unknown" } as never),
      ],
      total: 3,
    });
    render(<ApplicationList />);
    await screen.findAllByTestId("row-fit");
    const labels = screen.getAllByTestId("row-line-side");
    expect(labels).toHaveLength(2);
    expect(labels[0]).toHaveTextContent("Above your line");
    expect(labels[1]).toHaveTextContent("Below your line");
  });
});

describe("ApplicationList — In flight tab (weak spot #3 wave 2)", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("In flight fetches in_flight=true and renders the stage text", async () => {
    listApplications.mockImplementation((params: { in_flight?: boolean }) =>
      Promise.resolve(
        params.in_flight
          ? { applications: [summary({ id: 7, job_title: "Held Job", stage: "Reading the kit…" })], total: 1 }
          : {
              applications: [summary({ id: 7, job_title: "Held Job" }), summary({ id: 8, job_title: "Free Job" })],
              total: 2,
            }
      )
    );
    render(<ApplicationList />);
    await screen.findByText("Free Job");
    expect(listApplications).toHaveBeenLastCalledWith({ limit: 50 });
    fireEvent.click(screen.getByTestId("in-flight-filter"));
    await waitFor(() => expect(listApplications).toHaveBeenLastCalledWith({ limit: 50, in_flight: true }));
    expect(await screen.findByTestId("row-stage")).toHaveTextContent("Reading the kit…");
    expect(screen.queryByText("Free Job")).toBeNull();
  });

  it("an empty In flight tab keeps the tab row and says nothing is in flight", async () => {
    listApplications.mockImplementation((params: { in_flight?: boolean }) =>
      Promise.resolve(params.in_flight ? { applications: [], total: 0 } : { applications: [summary()], total: 1 })
    );
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    fireEvent.click(screen.getByTestId("in-flight-filter"));
    expect(await screen.findByText("Nothing is in flight right now.")).toBeInTheDocument();
    expect(screen.getByTestId("in-flight-filter")).toBeInTheDocument();
  });
});

describe("ApplicationList — Job priority (owner decision, 2026-10-19)", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("the priority select refetches with priority; All sends nothing", async () => {
    listApplications.mockResolvedValue({ applications: [summary()], total: 1 });
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    expect(listApplications).toHaveBeenLastCalledWith({ limit: 50 });

    fireEvent.change(screen.getByTestId("priority-filter"), { target: { value: "highest" } });
    await waitFor(() => expect(listApplications).toHaveBeenLastCalledWith({ limit: 50, priority: "highest" }));

    fireEvent.change(screen.getByTestId("priority-filter"), { target: { value: "not_set" } });
    await waitFor(() => expect(listApplications).toHaveBeenLastCalledWith({ limit: 50, priority: "not_set" }));

    fireEvent.change(screen.getByTestId("priority-filter"), { target: { value: "all" } });
    await waitFor(() => expect(listApplications).toHaveBeenLastCalledWith({ limit: 50 }));
  });

  it("priority and In flight combine, and an empty priority result keeps the select", async () => {
    listApplications.mockImplementation((params: { priority?: string }) =>
      Promise.resolve(params.priority ? { applications: [], total: 0 } : { applications: [summary()], total: 1 })
    );
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    fireEvent.click(screen.getByTestId("in-flight-filter"));
    fireEvent.change(screen.getByTestId("priority-filter"), { target: { value: "low" } });
    await waitFor(() =>
      expect(listApplications).toHaveBeenLastCalledWith({ limit: 50, in_flight: true, priority: "low" })
    );
    expect(await screen.findByText("Nothing is in flight right now.")).toBeInTheDocument();
    expect(screen.getByTestId("priority-filter")).toBeInTheDocument();
  });

  it("shows a tag for a set priority, not for off or unset", async () => {
    listApplications.mockResolvedValue({
      applications: [
        summary({ id: 1, job_title: "Top Job", priority: "highest" }),
        summary({ id: 2, job_title: "Off Job", priority: "off" }),
        summary({ id: 3, job_title: "Plain Job", priority: null }),
      ],
      total: 3,
    });
    render(<ApplicationList />);
    await screen.findByText("Top Job");
    const tags = screen.getAllByTestId("row-priority");
    expect(tags).toHaveLength(1);
    expect(tags[0]).toHaveTextContent("Highest priority");
  });
});

describe("ApplicationList — Queued filter (batch queue)", () => {
  beforeEach(() => {
    listApplications.mockReset();
  });

  it("asks for queued rows, shows the summary line, the place in line and the problems", async () => {
    listApplications.mockImplementation(async (params: { queued?: boolean }) =>
      params.queued
        ? {
            applications: [
              summary({
                id: 5, job_title: "Queued Job", queue_position: 1,
                preflight: { kit_missing: ["a"], has_cv: false, duplicate_flag: "", avoided: true, company_left: null, blocked_count: 2, hold: null },
              } as never),
            ],
            total: 1,
            queue_summary: { queued: 8, ready_to_send: 5, asked: 1, blocked: 1, daily_left: 3 },
          }
        : { applications: [summary()], total: 1 },
    );
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    fireEvent.click(screen.getByTestId("queued-filter"));
    // the old list never shows under the new filter while it loads
    expect(screen.queryByText("Staff Engineer")).toBeNull();
    await screen.findByText("Queued Job");
    expect(listApplications).toHaveBeenLastCalledWith({ limit: 50, queued: true });
    expect(screen.getByTestId("queue-summary")).toHaveTextContent("8 queued · 5 ready · 2 need you · 3 left today");
    expect(screen.getByTestId("row-queue-position")).toHaveTextContent("#1 in batch");
    const problems = screen.getByTestId("row-preflight");
    expect(problems).toHaveTextContent("Missing 1 answer");
    expect(problems).toHaveTextContent("No CV yet");
    expect(problems).toHaveTextContent("On your never-apply list");
    expect(problems).toHaveTextContent("Blocked 2 times");
  });

  it("says so when the batch is empty", async () => {
    listApplications.mockImplementation(async (params: { queued?: boolean }) =>
      params.queued ? { applications: [], total: 0, queue_summary: { queued: 0 } } : { applications: [summary()], total: 1 },
    );
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    fireEvent.click(screen.getByTestId("queued-filter"));
    await screen.findByText("Your batch is empty. Bring a job and leave Add to batch ticked, or let your AI hunt.");
  });

  it("shows a browser chip per queued row and a filter with counts that narrows the list", async () => {
    listApplications.mockImplementation(async (params: { queued?: boolean }) =>
      params.queued
        ? {
            applications: [
              summary({
                id: 5, job_title: "Greenhouse Job", queue_position: 1,
                browser: { kind: "job360", label: "I'll use Job360's browser", reason: "default" },
              } as never),
              summary({
                id: 6, job_title: "LinkedIn Job", queue_position: 2,
                browser: { kind: "own_browser", label: "Your own browser: runs when your PC is on", reason: "linkedin_own_browser" },
              } as never),
            ],
            total: 2,
            queue_summary: { queued: 2, by_browser: { job360: 1, own_browser: 1, human: 0 } },
          }
        : { applications: [summary()], total: 1 },
    );
    render(<ApplicationList />);
    await screen.findByText("Staff Engineer");
    fireEvent.click(screen.getByTestId("queued-filter"));
    await screen.findByText("Greenhouse Job");
    expect(screen.getByTestId("queue-browser-5")).toHaveAttribute("data-kind", "job360");
    expect(screen.getByTestId("queue-browser-6")).toHaveTextContent("Your own browser: runs when your PC is on");
    expect(screen.getByTestId("queue-filter-all")).toHaveTextContent("All 2");
    expect(screen.getByTestId("queue-filter-job360")).toHaveTextContent("Job360's browser 1");
    expect(screen.getByTestId("queue-filter-own_browser")).toHaveTextContent("Your own browser 1");
    expect(screen.queryByTestId("queue-filter-human")).toBeNull();
    fireEvent.click(screen.getByTestId("queue-filter-own_browser"));
    expect(screen.queryByText("Greenhouse Job")).toBeNull();
    expect(screen.getByText("LinkedIn Job")).toBeInTheDocument();
  });
});
