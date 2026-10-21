import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApplicationClient } from "@/app/applications/[id]/ApplicationClient";
import { card } from "@/components/needs-you/ready-fixtures";
import type { ApplicationDetail } from "@/lib/api";

const getApplication = vi.fn();
const getApplicationControls = vi.fn();
const getReadyToSend = vi.fn();
const approveSend = vi.fn();
const declineSend = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getApplication: (...a: unknown[]) => getApplication(...a),
  getApplicationControls: (...a: unknown[]) => getApplicationControls(...a),
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
  approveSend: (...a: unknown[]) => approveSend(...a),
  declineSend: (...a: unknown[]) => declineSend(...a),
  getAlignment: vi.fn().mockResolvedValue({ fit: null, skills_in_ad: [], skills_not_in_ad: [], skills_total: 0, ad_chars: 0 }),
  listAsks: vi.fn().mockResolvedValue({ asks: [], open_count: 0 }),
  getAssistantSettings: vi.fn().mockResolvedValue({ waiting: [], paused: false }),
  recordApplicationReceipt: vi.fn(),
  getApplicationArtifact: vi.fn(),
  getArtifactDiff: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function detail(): ApplicationDetail {
  return {
    id: 42, job_id: 900, status: "considering", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-15T00:00:00Z",
    last_event_at: null, interview_at: null,
    job: { job_title: "Staff Engineer", job_company: "Acme", job_location: "Remote", job_url: "", job_source: "user_brought", job_description_snapshot: "Build.", snapshot_at: "2026-09-01T00:00:00Z", catalog_present: true },
    fit: null,
    visa: { signal: "unknown", detail: "", country: "", recorded_by: "", recorded_at: "", needs_sponsorship: null },
    artifacts: [], contacts: [], events: [], receipts: [], asks: [],
    next_step: { code: "apply", label: "Apply" }, follow_up_on: null, follow_up_due: false,
  } as unknown as ApplicationDetail;
}

const MARK = { by: "web", where: "web", at: "2026-10-08T10:00:00Z" };
const controls = (over: Record<string, unknown> = {}) => ({
  application_id: 42,
  cv: { artifact_id: 5, version: 3, sha256: "x", seen: MARK, approved: null },
  declined: MARK,
  autofill: { mode: "unset", by: null, where: null, at: null },
  duplicate: { same_job: null, same_company_30d: 0, flag: "", cleared: null },
  ...over,
});
const ready = (items: unknown[]) => ({ paused: false, total: items.length, unflagged: items.length, items });

beforeEach(() => {
  getApplication.mockReset().mockResolvedValue(detail());
  getApplicationControls.mockReset().mockResolvedValue(controls());
  getReadyToSend.mockReset().mockResolvedValue(ready([card(42)]));
  approveSend.mockReset().mockResolvedValue(controls({ declined: null }));
  declineSend.mockReset().mockResolvedValue(controls());
});

describe("application page: Ready card", () => {
  it("asks for this application's card and shows it above the decisions", async () => {
    render(<ApplicationClient applicationId={42} />);
    const c = await screen.findByTestId("ready-card-42");
    expect(getReadyToSend).toHaveBeenCalledWith({ applicationId: 42 });
    const decisions = screen.getByTestId("app-decisions");
    expect(c.compareDocumentPosition(decisions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("Send passes the card's CV and fill ids; Don't send is the existing decline", async () => {
    render(<ApplicationClient applicationId={42} />);
    fireEvent.click(await screen.findByTestId("ready-send-42"));
    await waitFor(() => expect(approveSend).toHaveBeenCalledWith(42, { artifactId: 42, formFilledEventId: 420 }));
    fireEvent.click(screen.getByTestId("ready-decline-42"));
    await waitFor(() => expect(declineSend).toHaveBeenCalledWith(42));
  });

  it("no card when nothing is ready, or the read fails", async () => {
    getReadyToSend.mockResolvedValue(ready([]));
    const { unmount } = render(<ApplicationClient applicationId={42} />);
    await screen.findByTestId("app-decisions");
    expect(screen.queryByTestId("ready-card-42")).toBeNull();
    unmount();
    getReadyToSend.mockRejectedValue(new Error("down"));
    render(<ApplicationClient applicationId={42} />);
    await screen.findByTestId("app-decisions");
    expect(screen.queryByTestId("ready-card-42")).toBeNull();
  });

  it("an old 'Don't send' is not current after a newer fill", async () => {
    render(<ApplicationClient applicationId={42} />);
    const state = await screen.findByTestId("send-state");
    expect(state).toHaveTextContent("New fill waiting for your yes.");
    expect(state).toHaveTextContent("Before it: Don't send");
    // One decision UI: the card's guarded Send, never a second unguarded "Send this one".
    expect(screen.queryByTestId("send-approve-button")).toBeNull();
    expect(screen.queryByTestId("send-decline-button")).toBeNull();
  });

  it("a 409 on Send says it changed and re-reads the page", async () => {
    const { ApiError } = await import("@/lib/api-error");
    approveSend.mockRejectedValue(new ApiError(409, "changed"));
    const { toast } = await import("sonner");
    render(<ApplicationClient applicationId={42} />);
    fireEvent.click(await screen.findByTestId("ready-send-42"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("This changed since you looked. Check it again."));
    // re-read: this application's card again (the badge recount reads its own limit=0 too)
    await waitFor(() =>
      expect(getReadyToSend.mock.calls.filter(([p]) => (p as { applicationId?: number })?.applicationId === 42)).toHaveLength(2),
    );
  });

  it("with no newer fill the newest decision stays current", async () => {
    getReadyToSend.mockResolvedValue(ready([]));
    render(<ApplicationClient applicationId={42} />);
    const state = await screen.findByTestId("send-state");
    expect(state).toHaveTextContent(/^Don't send - /);
    expect(state).not.toHaveTextContent("New fill waiting");
    expect(screen.getByTestId("send-approve-button")).toBeInTheDocument();
  });
});
