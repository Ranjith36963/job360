import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApplicationClient } from "@/app/applications/[id]/ApplicationClient";
import type { ApplicationDetail } from "@/lib/api";

// Same mock shape as application-client-layout.test.tsx, plus the asks calls.
const getApplication = vi.fn();
const getAlignment = vi.fn();
const listAsks = vi.fn();
const answerAsk = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getApplication: (...args: unknown[]) => getApplication(...args),
  getAlignment: (...args: unknown[]) => getAlignment(...args),
  listAsks: (...args: unknown[]) => listAsks(...args),
  answerAsk: (...args: unknown[]) => answerAsk(...args),
  withdrawAsk: vi.fn(),
  recordApplicationReceipt: vi.fn(),
  setApplicationVisa: vi.fn(),
  addContact: vi.fn(),
  recordApplicationEvent: vi.fn(),
  getApplicationArtifact: vi.fn(),
  getArtifactDiff: vi.fn(),
  // S3: the decision buttons read this on mount; a failed read hides them.
  getApplicationControls: vi.fn().mockRejectedValue(new Error("not mocked")),
}));

const ask = (over: Record<string, unknown> = {}) => ({
  id: 7,
  question: "Do you need visa sponsorship for France?",
  context: "",
  status: "open",
  asked_by: "Claude",
  asked_at: "2026-10-03T09:12:00Z",
  answer: null,
  answered_at: null,
  answered_by: null,
  answered_by_user: false,
  withdrawn_at: null,
  application_id: 42,
  job_title: "Staff Engineer",
  job_company: "Acme",
  ...over,
});

function detail(over: Record<string, unknown> = {}): ApplicationDetail {
  return {
    id: 42,
    job_id: 900,
    status: "considering",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-15T00:00:00Z",
    last_event_at: null,
    interview_at: null,
    job: {
      job_title: "Staff Engineer",
      job_company: "Acme",
      job_location: "Remote",
      job_url: "",
      job_source: "user_brought",
      job_description_snapshot: "Build things.",
      snapshot_at: "2026-09-01T00:00:00Z",
      catalog_present: true,
    },
    fit: null,
    visa: { signal: "unknown", detail: "", country: "", recorded_by: "", recorded_at: "", needs_sponsorship: null },
    artifacts: [],
    contacts: [],
    events: [],
    receipts: [],
    asks: [],
    next_step: { code: "apply", label: "Apply" },
    follow_up_on: null,
    follow_up_due: false,
    ...over,
  } as unknown as ApplicationDetail;
}

const brought = {
  id: 1,
  corrects_event_id: null,
  detail: "",
  event_type: "brought",
  occurred_at: "2026-10-03T10:00:00Z",
  payload: {},
  recorded_at: "2026-10-03T10:00:00Z",
  recorded_by: "agent:Claude",
  scheduled_at: null,
  source: null,
  superseded: false,
};

describe("ApplicationClient frame", () => {
  beforeEach(() => {
    getApplication.mockReset();
    getAlignment.mockReset();
    listAsks.mockReset();
    answerAsk.mockReset();
    getAlignment.mockResolvedValue({ fit: null, skills_in_ad: [], skills_not_in_ad: [], skills_total: 0, ad_chars: 0 });
  });

  it("eyebrow names who brought the job and when", async () => {
    getApplication.mockResolvedValue(detail({ events: [brought] }));
    render(<ApplicationClient applicationId={42} />);
    const eyebrow = await screen.findByTestId("app-eyebrow");
    expect(eyebrow.textContent).toMatch(/^APP-042 · brought by Claude · .*2026$/);
  });

  it("eyebrow falls back to the id and created date without a brought event", async () => {
    getApplication.mockResolvedValue(detail());
    render(<ApplicationClient applicationId={42} />);
    const eyebrow = await screen.findByTestId("app-eyebrow");
    expect(eyebrow.textContent).toMatch(/^APP-042 · .*2026$/);
    expect(eyebrow.textContent).not.toMatch(/brought by/);
  });

  it("keeps one h1 and shows company and location", async () => {
    getApplication.mockResolvedValue(detail());
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Remote")).toBeInTheDocument();
  });

  it("shows only open, non-withdrawn asks, under a Needs you label", async () => {
    getApplication.mockResolvedValue(
      detail({
        asks: [
          ask(),
          ask({ id: 8, question: "Old one", status: "answered", answer: "x" }),
          ask({ id: 9, question: "Gone one", withdrawn_at: "2026-10-03T11:00:00Z" }),
        ],
      }),
    );
    render(<ApplicationClient applicationId={42} />);
    expect(await screen.findByText("Do you need visa sponsorship for France?")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Needs you" })).toBeInTheDocument();
    expect(screen.queryByText("Old one")).toBeNull();
    expect(screen.queryByText("Gone one")).toBeNull();
  });

  it("stays silent when there are no open asks, or the field is missing", async () => {
    const d = detail();
    delete (d as unknown as Record<string, unknown>).asks;
    getApplication.mockResolvedValue(d);
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");
    expect(screen.queryByTestId("app-asks")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Needs you" })).toBeNull();
  });

  it("answering reloads the application and announces the fresh open count", async () => {
    getApplication.mockResolvedValueOnce(detail({ asks: [ask()] })).mockResolvedValue(detail({ asks: [] }));
    answerAsk.mockResolvedValue(ask({ status: "answered", answer: "Yes" }));
    listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    const seen: number[] = [];
    const onEvent = (e: Event) => seen.push((e as CustomEvent<number>).detail);
    window.addEventListener("job360:asks-changed", onEvent);

    render(<ApplicationClient applicationId={42} />);
    fireEvent.change(await screen.findByTestId("ask-input-7"), { target: { value: "Yes" } });
    fireEvent.click(screen.getByTestId("ask-save"));
    fireEvent.click(await screen.findByTestId("ask-answer-confirm"));

    await waitFor(() => expect(seen).toEqual([0]));
    expect(answerAsk).toHaveBeenCalledWith(7, "Yes");
    expect(getApplication).toHaveBeenCalledTimes(2);
    expect(listAsks).toHaveBeenCalledWith("open");
    await waitFor(() => expect(screen.queryByTestId("app-asks")).toBeNull());
    window.removeEventListener("job360:asks-changed", onEvent);
  });
});
