import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApplicationList } from "./ApplicationList";
import type { ApplicationSummary } from "@/lib/api";

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
    next_step: { code: "judge_fit", label: "No fit judged yet — ask your agent to judge it" },
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
    expect(screen.getByTestId("row-follow-up")).toHaveTextContent("Follow up 3 Oct");
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
