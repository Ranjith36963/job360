import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ApplicationClient } from "@/app/applications/[id]/ApplicationClient";
import { sendableCard } from "@/components/needs-you/ready-fixtures";
import type { ApplicationDetail } from "@/lib/api";

const getApplication = vi.fn();
const getApplicationControls = vi.fn();
const getReadyToSend = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getApplication: (...a: unknown[]) => getApplication(...a),
  getApplicationControls: (...a: unknown[]) => getApplicationControls(...a),
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
  getCvToApprove: vi.fn().mockResolvedValue({ items: [] }),
  approveSend: vi.fn(),
  declineSend: vi.fn(),
  getAlignment: vi.fn().mockResolvedValue({ fit: null, skills_in_ad: [], skills_not_in_ad: [], skills_total: 0, ad_chars: 0 }),
  listAsks: vi.fn().mockResolvedValue({ asks: [], open_count: 0 }),
  getAssistantSettings: vi.fn().mockResolvedValue({ waiting: [], paused: false }),
  recordApplicationReceipt: vi.fn(),
  getApplicationArtifact: vi.fn(),
  getArtifactDiff: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const OPEN_ASK = {
  id: 7, application_id: 42, job_company: "Acme", job_title: "Staff Engineer", question: "Salary?", context: "",
  asked_by: "Claude", asked_at: "2026-10-20T10:00:00+00:00", status: "open", withdrawn_at: null,
};

function detail(over: Record<string, unknown> = {}): ApplicationDetail {
  return {
    id: 42, job_id: 900, status: "considering", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-15T00:00:00Z",
    last_event_at: null, interview_at: null,
    job: { job_title: "Staff Engineer", job_company: "Acme", job_location: "Remote", job_url: "", job_source: "user_brought", job_description_snapshot: "Build.", snapshot_at: "2026-09-01T00:00:00Z", catalog_present: true },
    fit: null,
    visa: { signal: "unknown", detail: "", country: "", recorded_by: "", recorded_at: "", needs_sponsorship: null },
    artifacts: [], contacts: [], events: [], receipts: [], asks: [],
    next_step: { code: "apply", label: "Apply" }, follow_up_on: null, follow_up_due: false,
    ...over,
  } as unknown as ApplicationDetail;
}
const MARK = { by: "web", where: "web", at: "2026-10-08T10:00:00Z" };
const controls = (blocked: unknown = null) => ({
  blocked,
  application_id: 42,
  cv: { artifact_id: 5, version: 3, sha256: "x", seen: MARK, approved: null },
  declined: null,
  autofill: { mode: "unset", by: null, where: null, at: null },
  duplicate: { same_job: null, same_company_30d: 0, flag: "", cleared: null },
});
const ready = (items: unknown[]) => ({ paused: false, total: items.length, unflagged: items.length, items });
const ev = (id: number, event_type: string) => ({ id, event_type, payload: {}, occurred_at: "2026-10-20T10:00:00Z", recorded_by: "web", superseded: false });

beforeEach(() => {
  getApplication.mockReset().mockResolvedValue(detail());
  getApplicationControls.mockReset().mockResolvedValue(controls());
  getReadyToSend.mockReset().mockResolvedValue(ready([]));
});

describe("application page: the form once", () => {
  it("shows the filled form once, inside the top Needs-you section, never in the side column", async () => {
    getApplication.mockResolvedValue(detail({ asks: [OPEN_ASK] }));
    getReadyToSend.mockResolvedValue(ready([sendableCard(42)]));
    render(<ApplicationClient applicationId={42} />);
    const top = await screen.findByTestId("app-asks");
    await within(top).findByTestId("ready-card-42");
    expect(screen.getAllByTestId("ready-card-42")).toHaveLength(1);
    expect(screen.getAllByText("Why Poolside?")).toHaveLength(1);
    expect(within(screen.getByTestId("app-col-side")).queryByTestId("ready-card-42")).toBeNull();
    expect(screen.getAllByTestId("ready-send-42")).toHaveLength(1);
    expect(screen.queryByTestId("send-approve-button")).toBeNull();
  });

  it("with no job card the form still sits in the top section", async () => {
    getReadyToSend.mockResolvedValue(ready([sendableCard(42)]));
    render(<ApplicationClient applicationId={42} />);
    const top = await screen.findByTestId("app-asks");
    expect(await within(top).findByTestId("ready-card-42")).toBeInTheDocument();
    expect(screen.getAllByTestId("ready-card-42")).toHaveLength(1);
  });

  it("a job the server reports blocked shows no Send", async () => {
    getApplicationControls.mockResolvedValue(controls({ reason: "captcha", reason_label: "CAPTCHA", step: "", page_host: "", detail: "", by: "agent", at: "2026-10-20T10:00:00Z" }));
    render(<ApplicationClient applicationId={42} />);
    await screen.findByTestId("app-decisions");
    expect(screen.queryByTestId("send-approve-button")).toBeNull();
    expect(screen.queryByTestId("send-decline-button")).toBeNull();
  });

  it("an upload_failed block cleared by a later document_uploaded shows Send (server says not blocked)", async () => {
    getApplication.mockResolvedValue(detail({ events: [{ ...ev(1, "blocked"), payload: { reason: "upload_failed" } }, ev(2, "document_uploaded")] }));
    getApplicationControls.mockResolvedValue(controls(null));
    render(<ApplicationClient applicationId={42} />);
    expect(await screen.findByTestId("send-approve-button")).toBeInTheDocument();
  });

  it("Next line shows the first blocker and how many more, else the stored next step", async () => {
    getApplication.mockResolvedValue(detail({ asks: [OPEN_ASK], next: { kind: "captcha", actor: "you", label: "solve the CAPTCHA", since: "2026-10-20T10:00:00Z" }, more: 2 }));
    const { unmount } = render(<ApplicationClient applicationId={42} />);
    await screen.findByTestId("app-asks");
    expect(screen.getByTestId("next-step")).toHaveTextContent("Next: You: solve the CAPTCHA +2 more");
    unmount();
    getApplication.mockResolvedValue(detail());
    render(<ApplicationClient applicationId={42} />);
    expect(await screen.findByTestId("next-step")).toHaveTextContent("Next: Apply");
  });
});
