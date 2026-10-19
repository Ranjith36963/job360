import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApplicationClient } from "@/app/applications/[id]/ApplicationClient";
import type { ApplicationDetail } from "@/lib/api";

const getApplication = vi.fn();
const releaseHold = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getApplication: (...a: unknown[]) => getApplication(...a),
  releaseHold: (...a: unknown[]) => releaseHold(...a),
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

const HOLD = {
  held_by: "agent:Claude", session: "desktop-apply", since: "2026-10-15T10:00:00+00:00",
  until: "2026-10-15T10:30:00+00:00", unsure: false, detail: null,
};

beforeEach(() => {
  getApplication.mockReset();
  releaseHold.mockReset().mockResolvedValue({ released: true });
});

describe("application page: held by another session", () => {
  it("shows the holder and session, and Release posts once then hides the banner", async () => {
    getApplication
      .mockResolvedValueOnce(detail({ hold: HOLD, stage: "Form filled" }))
      .mockResolvedValue(detail());
    render(<ApplicationClient applicationId={42} />);
    const banner = await screen.findByTestId("app-hold");
    expect(banner).toHaveTextContent("agent:Claude / desktop-apply");
    expect(banner).toHaveTextContent("Form filled");
    fireEvent.click(screen.getByRole("button", { name: "Release" }));
    await waitFor(() => expect(screen.queryByTestId("app-hold")).toBeNull());
    expect(releaseHold).toHaveBeenCalledTimes(1);
    expect(releaseHold).toHaveBeenCalledWith(42);
  });

  it("an unsure hold reads softer and has no Release button", async () => {
    getApplication.mockResolvedValue(detail({ hold: { ...HOLD, unsure: true, detail: "maybe" } }));
    render(<ApplicationClient applicationId={42} />);
    const banner = await screen.findByTestId("app-hold");
    expect(banner).toHaveTextContent("may be working");
    expect(screen.queryByRole("button", { name: "Release" })).toBeNull();
  });

  it("no hold, no banner", async () => {
    getApplication.mockResolvedValue(detail());
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");
    expect(screen.queryByTestId("app-hold")).toBeNull();
  });
});
