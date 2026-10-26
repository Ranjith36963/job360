import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import { JobCards, loadJobCard } from "./JobCards";

const getApplication = vi.fn();
const listPeople = vi.fn();
const getCvToApprove = vi.fn();
vi.mock("@/lib/api", () => ({
  getApplication: (...a: unknown[]) => getApplication(...a),
  listPeople: (...a: unknown[]) => listPeople(...a),
  getCvToApprove: (...a: unknown[]) => getCvToApprove(...a),
}));
vi.mock("./JobCard", () => ({
  JobCard: ({ card }: { card: { applicationId: number; company: string } }) => (
    <li data-testid={`card-${card.applicationId}`}>{card.company}</li>
  ),
}));

function detail(id: number, asks: Ask[] = []) {
  return {
    id, asks, contacts: [], artifacts: [], form_state: "", updated_at: "2026-10-20T10:00:00+00:00",
    job: { job_company: `Co${id}`, job_title: `Role${id}`, job_url: "https://x.example/" },
  };
}
function ask(id: number, app: number, over: Partial<Ask> = {}): Ask {
  return {
    id, application_id: app, job_company: `Co${app}`, job_title: `Role${app}`, question: "Salary?", context: "",
    asked_by: "Claude", asked_at: `2026-10-20T10:${String(id % 60).padStart(2, "0")}:00+00:00`,
    status: "open", withdrawn_at: null, ...over,
  } as Ask;
}
const twenty = Array.from({ length: 20 }, (_, i) => ask(i + 1, i + 1));

let inFlight = 0;
let peak = 0;
beforeEach(() => {
  inFlight = 0;
  peak = 0;
  getApplication.mockReset().mockImplementation(async (id: number) => {
    inFlight++;
    peak = Math.max(peak, inFlight);
    await Promise.resolve();
    inFlight--;
    return detail(id);
  });
  listPeople.mockReset().mockResolvedValue({ people: [] });
  getCvToApprove.mockReset().mockResolvedValue({ items: [] });
});

describe("JobCards", () => {
  it("renders every header at once but loads only the first 8 jobs", async () => {
    render(<JobCards open={twenty} onChanged={async () => {}} />);
    for (let i = 1; i <= 20; i++) expect(screen.getByTestId(`job-header-${i}`)).toBeInTheDocument();
    expect(screen.getByTestId("job-skeleton-1")).toBeInTheDocument();
    expect(screen.getByTestId("jobs-count")).toHaveTextContent("20 jobs");
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    expect(getApplication).toHaveBeenCalledTimes(8);
    expect(peak).toBeLessThanOrEqual(8);
    expect(screen.getAllByTestId(/^job-header-/)).toHaveLength(12);
  });

  it("Show 8 more jobs loads the next 8, then the rest", async () => {
    render(<JobCards open={twenty} onChanged={async () => {}} />);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    fireEvent.click(screen.getByTestId("more-jobs"));
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(16));
    expect(getApplication).toHaveBeenCalledTimes(16);
    expect(screen.getByTestId("more-jobs")).toHaveTextContent("Show 4 more jobs");
    fireEvent.click(screen.getByTestId("more-jobs"));
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(20));
    expect(screen.queryByTestId("more-jobs")).toBeNull();
    expect(peak).toBeLessThanOrEqual(8);
  });

  it("never has more than 8 getApplication calls in flight", async () => {
    const release: (() => void)[] = [];
    getApplication.mockReset().mockImplementation((id: number) => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      return new Promise((res) => release.push(() => { inFlight--; res(detail(id)); }));
    });
    render(<JobCards open={twenty} onChanged={async () => {}} />);
    await waitFor(() => expect(release).toHaveLength(8));
    expect(getApplication).toHaveBeenCalledTimes(8);
    act(() => release.splice(0).forEach((r) => r()));
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    expect(peak).toBe(8);
  });

  it("puts a blocker job first on the first page", async () => {
    const asks = [...twenty.slice(0, 19), ask(99, 20, { kind: "captcha" })];
    render(<JobCards open={asks} onChanged={async () => {}} />);
    await waitFor(() => expect(screen.getByTestId("card-20")).toBeInTheDocument());
    expect(getApplication.mock.calls.map((c) => c[0])).toContain(20);
  });

  it("lists jobs whose only open item is someone to reach, after the ask jobs, loaded lazily", async () => {
    listPeople.mockResolvedValue({
      people: [{ message_count: 1, last_sent: null, jobs: [{ application_id: 500, job_company: "Reach Co", job_title: "Dev" }] }],
    });
    getApplication.mockImplementation(async (id: number) => ({
      ...detail(id),
      contacts: id === 500
        ? [{ id: 1, name: "Pat", company: "", linkedin_url: "", outreach: { messages: [{ text: "Hi", occurred_at: "2026-10-20T10:00:00+00:00" }], last_sent: null } }]
        : [],
    }));
    render(<JobCards open={[ask(1, 1)]} onChanged={async () => {}} />);
    expect(await screen.findByTestId("card-500")).toBeInTheDocument();
    const order = screen.getAllByTestId(/^card-|^job-header-/).map((e) => e.getAttribute("data-testid"));
    expect(order.indexOf("card-500")).toBeGreaterThan(order.findIndex((t) => t === "card-1"));
  });

  it("says what waits in each collapsed header", () => {
    getApplication.mockImplementation(() => new Promise(() => {})); // headers stay collapsed
    const a = ask(1, 1, { kind: "captcha" } as Partial<Ask>);
    const b = ask(2, 2, { kind: "login" } as Partial<Ask>);
    const c = ask(3, 2, { kind: "question" } as Partial<Ask>);
    render(<JobCards open={[a, b, c]} onChanged={async () => {}} />);
    expect(screen.getByTestId("job-header-1")).toHaveTextContent("Solve a CAPTCHA");
    expect(screen.getByTestId("job-header-2")).toHaveTextContent("Sign in · Check 1 answer");
    expect(screen.getByTestId("job-header-1")).not.toHaveTextContent(/things? wait/);
  });

  it("a collapsed header title links to the application", () => {
    getApplication.mockImplementation(() => new Promise(() => {}));
    render(<JobCards open={[ask(1, 1, { kind: "captcha" } as Partial<Ask>)]} onChanged={async () => {}} />);
    expect(screen.getByTestId("title-link-1")).toHaveAttribute("href", "/applications/1");
  });

  it("a people-only header reads Send notes", async () => {
    getApplication.mockImplementation(() => new Promise(() => {}));
    listPeople.mockResolvedValue({
      people: [{ message_count: 1, last_sent: null, jobs: [{ application_id: 500, job_company: "Reach Co", job_title: "Dev" }] }],
    });
    render(<JobCards open={twenty.slice(0, 8)} onChanged={async () => {}} />);
    expect(await screen.findByTestId("job-header-500")).toHaveTextContent("Send notes");
  });

  it("people-only jobs beyond the first page wait for Show more", async () => {
    listPeople.mockResolvedValue({
      people: [{ message_count: 1, last_sent: null, jobs: [{ application_id: 500, job_company: "Reach Co", job_title: "Dev" }] }],
    });
    render(<JobCards open={twenty.slice(0, 8)} onChanged={async () => {}} />);
    expect(await screen.findByTestId("job-header-500")).toHaveTextContent("Reach Co · Dev");
    await waitFor(() => expect(screen.getAllByTestId(/^card-/).length).toBeGreaterThan(0));
    expect(getApplication).not.toHaveBeenCalledWith(500);
  });

  it("a job whose record fails drops its header and reports its asks", async () => {
    getApplication.mockImplementation(async (id: number) => {
      if (id === 2) throw new Error("boom");
      return detail(id);
    });
    const onReport = vi.fn();
    render(<JobCards open={twenty.slice(0, 3)} onChanged={async () => {}} onReport={onReport} />);
    await waitFor(() => expect(screen.queryByTestId("job-header-2")).toBeNull());
    await waitFor(() => expect(onReport.mock.calls.at(-1)![0].failed.map((a: Ask) => a.id)).toEqual([2]));
    expect(onReport.mock.calls.at(-1)![0]).toMatchObject({ total: 2, settled: true });
  });
});

describe("loadJobCard (single application)", () => {
  it("builds the card from the application's own open asks", async () => {
    getCvToApprove.mockResolvedValue({ items: [] });
    const a = ask(1, 7);
    const card = await loadJobCard({ ...detail(7, [a]) } as never);
    expect(card?.applicationId).toBe(7);
    expect(card?.asks).toHaveLength(1);
    expect(getApplication).not.toHaveBeenCalled();
  });

  it("is null when nothing is open", async () => {
    expect(await loadJobCard(detail(7, [ask(2, 7, { status: "answered" })]) as never)).toBeNull();
  });
});
