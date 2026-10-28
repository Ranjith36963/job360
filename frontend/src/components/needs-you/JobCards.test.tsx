import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import type { JobCardData } from "@/lib/job-cards";
import { JobCards, loadJobCard } from "./JobCards";
import { answer as readyAnswer, card as readyCard } from "./ready-fixtures";

const getApplication = vi.fn();
const listPeople = vi.fn();
const getCvToApprove = vi.fn();
const getReadyToSend = vi.fn();
vi.mock("@/lib/api", () => ({
  getApplication: (...a: unknown[]) => getApplication(...a),
  listPeople: (...a: unknown[]) => listPeople(...a),
  getCvToApprove: (...a: unknown[]) => getCvToApprove(...a),
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
}));
vi.mock("./JobCard", () => ({
  JobCard: ({ card, ready }: { card: JobCardData; ready?: React.ReactNode }) => (
    <li data-testid={`card-${card.applicationId}`}>
      {card.company}
      {card.cv && <span data-testid={`cv-${card.applicationId}`}>CV v{card.cv.version}</span>}
      {card.asks.map(({ ask }) => <span key={ask.id} data-testid={`ask-${ask.id}`} />)}
      {ready}
    </li>
  ),
}));
const readyList = (items: ReturnType<typeof readyCard>[]) => ({ paused: false, total: items.length, unflagged: items.length, items });
const cvRow = (id: number, saved_at = "2026-10-20T09:00:00+00:00") => ({
  application_id: id, job_title: `Role${id}`, job_company: `Co${id}`, cv: { artifact_id: id * 10, version: 2, saved_at, made_by: "agent:X" },
});

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
  getReadyToSend.mockReset().mockResolvedValue(readyList([]));
});

describe("JobCards", () => {
  it("renders every header at once but loads only the first 8 jobs", async () => {
    const onReport = vi.fn();
    render(<JobCards open={twenty} onChanged={async () => {}} onReport={onReport} />);
    // Only the first page shows, each as a placeholder while its card loads (newest first).
    for (let i = 13; i <= 20; i++) expect(screen.getByTestId(`job-header-${i}`)).toBeInTheDocument();
    expect(screen.queryByTestId("job-header-12")).toBeNull();
    expect(screen.getByTestId("job-skeleton-20")).toBeInTheDocument();
    expect(onReport.mock.calls.at(-1)![0].total).toBe(20);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    expect(getApplication).toHaveBeenCalledTimes(8);
    expect(peak).toBeLessThanOrEqual(8);
    expect(screen.queryAllByTestId(/^job-header-/)).toHaveLength(0); // no header-only rows
    expect(screen.getByTestId("more-jobs")).toHaveTextContent("Show 8 more · 12 left");
  });

  it("Show 8 more jobs loads the next 8, then the rest", async () => {
    render(<JobCards open={twenty} onChanged={async () => {}} />);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    fireEvent.click(screen.getByTestId("more-jobs"));
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(16));
    expect(getApplication).toHaveBeenCalledTimes(16);
    expect(screen.getByTestId("more-jobs")).toHaveTextContent("Show 4 more · 4 left");
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
    render(<JobCards open={twenty.slice(0, 7)} onChanged={async () => {}} />);
    expect(await screen.findByTestId("job-header-500")).toHaveTextContent("Send notes");
  });

  it("people-only jobs beyond the first page wait for Show more", async () => {
    listPeople.mockResolvedValue({
      people: [{ message_count: 1, last_sent: null, jobs: [{ application_id: 500, job_company: "Reach Co", job_title: "Dev" }] }],
    });
    render(<JobCards open={twenty.slice(0, 8)} onChanged={async () => {}} />);
    expect(await screen.findByTestId("more-jobs")).toHaveTextContent("Show 1 more · 1 left");
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    expect(screen.queryByTestId("job-header-500")).toBeNull(); // no header-only row for it
    expect(getApplication).not.toHaveBeenCalledWith(500);
    fireEvent.click(screen.getByTestId("more-jobs"));
    await waitFor(() => expect(getApplication).toHaveBeenCalledWith(500));
  });

  it("52 jobs show 8 full cards, one Show more button and zero header-only rows", async () => {
    const many = Array.from({ length: 52 }, (_, i) => ask(i + 1, i + 1));
    const onReport = vi.fn();
    render(<JobCards open={many} onChanged={async () => {}} onReport={onReport} />);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(8));
    expect(screen.queryAllByTestId(/^job-header-/)).toHaveLength(0);
    expect(screen.getAllByTestId("more-jobs")).toHaveLength(1);
    expect(screen.getByTestId("more-jobs")).toHaveTextContent("Show 8 more · 44 left");
    expect(onReport.mock.calls.at(-1)![0].total).toBe(52);
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

describe("one card per job (owner 2026-10-26)", () => {
  it("the summary line counts every job once: asks, ready to send, CV to approve", async () => {
    getReadyToSend.mockResolvedValue(readyList([readyCard(1), readyCard(30)]));
    getCvToApprove.mockResolvedValue({ items: [cvRow(2), cvRow(31)] });
    const onReport = vi.fn();
    render(<JobCards open={[ask(1, 1), ask(2, 2), ask(3, 3)]} onChanged={async () => {}} onReport={onReport} />);
    await waitFor(() => expect(onReport.mock.calls.at(-1)![0]).toMatchObject({ total: 5, settled: true }));
  });

  it("a job with a CV, a ready-to-send form and questions renders ONE card holding all three", async () => {
    getReadyToSend.mockResolvedValue(readyList([readyCard(7)]));
    getCvToApprove.mockResolvedValue({ items: [cvRow(7)] });
    render(<JobCards open={[ask(1, 7), ask(2, 7, { kind: "captcha" })]} onChanged={async () => {}} />);
    const one = await screen.findByTestId("card-7");
    await waitFor(() => expect(one).toContainElement(screen.getByTestId("cv-7")));
    expect(one).toContainElement(screen.getByTestId("ready-card-7"));
    expect(one).toContainElement(screen.getByTestId("ask-1"));
    expect(one).toContainElement(screen.getByTestId("ask-2"));
    expect(screen.getAllByTestId(/^card-|^job-header-/)).toHaveLength(1);
    expect(screen.getAllByTestId(/^ready-card-/)).toHaveLength(1);
    expect(screen.queryByTestId("title-link-7")).toBeNull(); // the embedded ready card adds no second title
    expect(screen.getByTestId("ready-card-7").textContent).not.toMatch(/APP-|score 82|prepared this/);
  });

  it("orders most urgent first: held form, clean ready, stuck on you, quick checks; newest first", async () => {
    getReadyToSend.mockResolvedValue(readyList([readyCard(31), readyCard(20, { answers: [readyAnswer()] })]));
    getCvToApprove.mockResolvedValue({ items: [cvRow(40)] });
    render(<JobCards open={[ask(2, 30), ask(1, 10, { kind: "captcha", held_seconds: 600 } as Partial<Ask>)]} onChanged={async () => {}} />);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(5));
    expect(screen.getAllByTestId(/^card-|^job-header-/).map((e) => e.dataset.testid)).toEqual(
      ["card-10", "card-20", "card-30", "card-40", "card-31"],
    );
  });

  it("a chip shows only its jobs, and Oldest first reverses the dates", async () => {
    getReadyToSend.mockResolvedValue(readyList([readyCard(20, { answers: [readyAnswer()] })]));
    getCvToApprove.mockResolvedValue({ items: [cvRow(40, "2026-10-21T09:00:00+00:00")] });
    const asks = [ask(1, 10), ask(2, 30)];
    const { rerender } = render(<JobCards open={asks} onChanged={async () => {}} show="checks" />);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/).map((e) => e.dataset.testid)).toEqual(["card-40"]));
    rerender(<JobCards open={asks} onChanged={async () => {}} sort="oldest" />);
    await waitFor(() => expect(screen.getAllByTestId(/^card-/)).toHaveLength(4));
    // Asks at 10:01 and 10:02, the fill on 10-08, the CV on 10-21.
    expect(screen.getAllByTestId(/^card-/).map((e) => e.dataset.testid)).toEqual(["card-20", "card-10", "card-30", "card-40"]);
  });
});

describe("folded rows (owner 2026-10-27: + unfolds the card)", () => {
  const brought = { id: 9, event_type: "brought", recorded_by: "agent:Helper", occurred_at: "2026-10-25T09:00:00+00:00" };
  function emplay(id: number, asks: Ask[] = []) {
    return { ...detail(id, asks), created_at: "2026-10-24T09:00:00+00:00", events: [brought], fit: { score: 74 },
      job: { job_company: "Emplay", job_title: "Associate AI Engineer", job_url: "https://wellfound.com/jobs/1",
        job_location: "Bengaluru", country: "IN", found_on: "linkedin" } };
  }

  it("every job starts folded: one summary line with company, role, place, age, who brought it, when, where from, fit and the next step", async () => {
    getApplication.mockImplementation(async (id: number) => emplay(id));
    render(<JobCards open={[ask(1, 3, { kind: "question", asked_at: new Date(Date.now() - 86400000).toISOString() })]} onChanged={async () => {}} />);
    const row = await screen.findByTestId("job-summary-3");
    expect(screen.getByTestId("job-row-3-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("card-3").parentElement).not.toBeVisible();
    for (const text of ["Emplay", "Associate AI Engineer", "Bengaluru", "since 1 d ago", "by Helper · 25 Oct · LinkedIn", "Fit 74", "Waiting on your answer", "Check 1 answer"])
      expect(row).toHaveTextContent(text);
    expect(row.textContent).not.toMatch(/APP-/);
  });

  it("+ unfolds and − folds it again; a click on the row toggles too, a click on its link does not", async () => {
    getApplication.mockImplementation(async (id: number) => emplay(id));
    render(<JobCards open={[ask(1, 3)]} onChanged={async () => {}} />);
    await screen.findByTestId("job-summary-3");
    const toggle = screen.getByTestId("job-row-3-toggle");
    expect(toggle).toHaveAccessibleName("Unfold Emplay, Associate AI Engineer");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("card-3")).toBeVisible();
    fireEvent.click(screen.getByTestId("job-summary-3"));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByTestId("row-title-3"));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("a live refresh never folds a card the user has open", async () => {
    getApplication.mockImplementation(async (id: number) => emplay(id));
    const { rerender } = render(<JobCards open={[ask(1, 3), ask(2, 4)]} onChanged={async () => {}} />);
    await screen.findByTestId("job-summary-3");
    fireEvent.click(screen.getByTestId("job-row-3-toggle"));
    rerender(<JobCards open={[ask(1, 3), ask(2, 4), ask(5, 6)]} onChanged={async () => {}} />);
    await screen.findByTestId("job-row-6");
    expect(screen.getByTestId("job-row-3-toggle")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("job-row-4-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  it("a folded held form still offers Finish it here; a sign-in step offers Open the form", async () => {
    getApplication.mockImplementation(async (id: number) => emplay(id));
    const held = ask(1, 3, { kind: "captcha", finish_here: true, held_seconds: 600 } as Partial<Ask>);
    render(<JobCards open={[held, ask(2, 4, { kind: "login" } as Partial<Ask>)]} onChanged={async () => {}} />);
    expect(await screen.findByTestId("row-finish-3")).toHaveTextContent("Finish it here");
    expect(screen.getByTestId("job-summary-3")).toHaveTextContent("Form open now");
    expect(await screen.findByTestId("row-form-4")).toHaveAttribute("href", "https://wellfound.com/jobs/1");
    fireEvent.click(screen.getByTestId("row-finish-3"));
    expect(screen.getByTestId("job-row-3-toggle")).toHaveAttribute("aria-expanded", "true");
  });
});
