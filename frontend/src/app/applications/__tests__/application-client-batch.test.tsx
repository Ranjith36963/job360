import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApplicationClient } from "@/app/applications/[id]/ApplicationClient";
import type { ApplicationDetail } from "@/lib/api";

const getApplication = vi.fn();
const recordApplicationEvent = vi.fn();
const resolveSubmit = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getApplication: (...a: unknown[]) => getApplication(...a),
  recordApplicationEvent: (...a: unknown[]) => recordApplicationEvent(...a),
  resolveSubmit: (...a: unknown[]) => resolveSubmit(...a),
  releaseHold: vi.fn(),
  getApplicationControls: vi.fn().mockRejectedValue(new Error("no controls")),
  getReadyToSend: vi.fn().mockResolvedValue({ paused: false, total: 0, unflagged: 0, items: [] }),
  getAlignment: vi.fn().mockResolvedValue({ fit: null, skills_in_ad: [], skills_not_in_ad: [], skills_total: 0, ad_chars: 0 }),
  listAsks: vi.fn().mockResolvedValue({ asks: [], open_count: 0 }),
  getAssistantSettings: vi.fn().mockResolvedValue({ waiting: [], paused: false }),
  recordApplicationReceipt: vi.fn(),
  getApplicationArtifact: vi.fn(),
  getArtifactDiff: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ev = (id: number, event_type: string) => ({
  id, event_type, payload: {}, detail: "", occurred_at: "2026-10-17T10:00:00Z", recorded_at: "2026-10-17T10:00:00Z",
  recorded_by: "agent:Claude", scheduled_at: null, corrects_event_id: null, source: null, superseded: false,
});

function detail(over: Record<string, unknown> = {}): ApplicationDetail {
  return {
    id: 42, job_id: 900, status: "considering", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-15T00:00:00Z",
    last_event_at: null, interview_at: null,
    job: { job_title: "Staff Engineer", job_company: "Acme", job_location: "Remote", job_url: "", job_source: "user_brought", job_description_snapshot: "Build.", snapshot_at: "2026-09-01T00:00:00Z", catalog_present: true },
    fit: null,
    visa: { signal: "unknown", detail: "", country: "", recorded_by: "", recorded_at: "", needs_sponsorship: null },
    artifacts: [], contacts: [], events: [], receipts: [], asks: [],
    next_step: { code: "apply", label: "Apply" }, follow_up_on: null, follow_up_due: false,
    hold: null, stage: "",
    ...over,
  } as unknown as ApplicationDetail;
}

beforeEach(() => {
  getApplication.mockReset();
  recordApplicationEvent.mockReset().mockResolvedValue({});
  resolveSubmit.mockReset().mockResolvedValue({});
});

describe("application page: started, never confirmed", () => {
  it("shows the banner while the newest submit event is 'started', and 'It was not sent' clears it", async () => {
    getApplication
      .mockResolvedValueOnce(detail({ events: [ev(1, "submit_attempted")] }))
      .mockResolvedValue(detail({ events: [ev(1, "submit_attempted"), ev(2, "submit_resolved")] }));
    render(<ApplicationClient applicationId={42} />);
    const banner = await screen.findByTestId("submit-unconfirmed");
    expect(banner).toHaveTextContent("never confirmed");
    fireEvent.click(screen.getByTestId("submit-not-sent"));
    await waitFor(() => expect(screen.queryByTestId("submit-unconfirmed")).toBeNull());
    expect(resolveSubmit).toHaveBeenCalledWith(42);
  });

  it("no banner after a later applied event", async () => {
    getApplication.mockResolvedValue(detail({ events: [ev(1, "submit_attempted"), ev(2, "applied")] }));
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");
    expect(screen.queryByTestId("submit-unconfirmed")).toBeNull();
  });
});

describe("application page: batch", () => {
  it("'Add to batch' records queued and the button turns into 'Remove from batch'", async () => {
    getApplication
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(detail({ events: [ev(1, "queued")] }));
    render(<ApplicationClient applicationId={42} />);
    fireEvent.click(await screen.findByTestId("batch-add"));
    await screen.findByTestId("batch-remove");
    expect(recordApplicationEvent).toHaveBeenCalledWith(42, { event_type: "queued" });
  });

  it("is not offered once the application is past considering", async () => {
    getApplication.mockResolvedValue(detail({ status: "applied" }));
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");
    expect(screen.queryByTestId("batch-add")).toBeNull();
    expect(screen.queryByTestId("batch-remove")).toBeNull();
  });
});
