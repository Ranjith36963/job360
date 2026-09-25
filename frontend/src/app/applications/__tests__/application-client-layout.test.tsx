import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApplicationClient } from "@/app/applications/[id]/ApplicationClient";
import type { ApplicationDetail } from "@/lib/api";

// Same pattern as src/components/applications/ArtifactVersions.test.tsx:
// mock every "@/lib/api" export the render tree can reach. Only
// getApplication and getAlignment fire on mount; the rest just need to
// exist so the child components' module-level imports resolve.
const getApplication = vi.fn();
const getAlignment = vi.fn();
const recordApplicationReceipt = vi.fn();
const setApplicationVisa = vi.fn();
const addContact = vi.fn();
const recordApplicationEvent = vi.fn();
const getApplicationArtifact = vi.fn();
const getArtifactDiff = vi.fn();

vi.mock("@/lib/api", () => ({
  getApplication: (...args: unknown[]) => getApplication(...args),
  getAlignment: (...args: unknown[]) => getAlignment(...args),
  recordApplicationReceipt: (...args: unknown[]) => recordApplicationReceipt(...args),
  setApplicationVisa: (...args: unknown[]) => setApplicationVisa(...args),
  addContact: (...args: unknown[]) => addContact(...args),
  recordApplicationEvent: (...args: unknown[]) => recordApplicationEvent(...args),
  getApplicationArtifact: (...args: unknown[]) => getApplicationArtifact(...args),
  getArtifactDiff: (...args: unknown[]) => getArtifactDiff(...args),
}));

type DetailWithNextStep = ApplicationDetail & {
  next_step: { code: string; label: string };
};

function detail(): DetailWithNextStep {
  return {
    id: 42,
    job_id: 900,
    status: "applied",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-15T00:00:00Z",
    last_event_at: "2026-09-14T00:00:00Z",
    interview_at: null,
    job: {
      job_title: "Staff Engineer",
      job_company: "Acme",
      job_location: "Remote",
      job_url: "https://acme.example/careers/1",
      job_source: "user_brought",
      job_description_snapshot: "Build things.",
      snapshot_at: "2026-09-01T00:00:00Z",
      catalog_present: true,
    },
    fit: null,
    visa: {
      signal: "unknown",
      detail: "",
      country: "",
      recorded_by: "",
      recorded_at: "",
      needs_sponsorship: null,
    },
    artifacts: [
      {
        id: 1,
        kind: "cv",
        version_no: 1,
        made_by: "agent",
        model: "claude",
        profile_version: 1,
        label: "",
        chars: 500,
        created_at: "2026-09-02T00:00:00Z",
        text: null,
        truncated: false,
      },
    ],
    contacts: [],
    events: [
      {
        id: 5,
        corrects_event_id: null,
        detail: "Mention the Kubernetes cert next time.",
        event_type: "lesson",
        occurred_at: "2026-09-14T00:00:00Z",
        payload: {},
        recorded_at: "2026-09-14T00:00:00Z",
        recorded_by: "user",
        scheduled_at: null,
        source: null,
        superseded: false,
      },
    ],
    receipts: [
      {
        id: 9,
        channel: "company site",
        confirmation: "",
        cover_letter_artifact_id: null,
        cv_artifact_id: 1,
        fields_filled: {},
        note: "",
        sent_at: "2026-09-10T00:00:00Z",
      },
    ],
    next_step: { code: "apply", label: "CV ready — apply, then mark it applied" },
    follow_up_on: null,
    follow_up_due: false,
  };
}

/** True when `a` comes before `b` in document order. */
function isBefore(a: Element, b: Element): boolean {
  return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("ApplicationClient — six-question layout", () => {
  beforeEach(() => {
    getApplication.mockReset();
    getAlignment.mockReset();
    getApplication.mockResolvedValue(detail());
    getAlignment.mockResolvedValue({
      fit: null,
      skills_in_ad: [],
      skills_not_in_ad: [],
      skills_total: 0,
      ad_chars: 0,
    });
  });

  it("left column (main) orders fit -> documents -> sent -> timeline", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    const main = screen.getByTestId("app-col-main");
    const order = [
      "section-fit",
      "section-documents",
      "section-sent",
      "section-timeline",
    ].map((id) => screen.getByTestId(id));

    for (const el of order) {
      expect(main.contains(el)).toBe(true);
    }
    for (let i = 0; i < order.length - 1; i++) {
      expect(isBefore(order[i], order[i + 1])).toBe(true);
    }
  });

  it("right column (side) orders visa -> people -> lessons", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    const side = screen.getByTestId("app-col-side");
    const order = ["section-visa", "section-people", "section-lessons"].map((id) =>
      screen.getByTestId(id)
    );

    for (const el of order) {
      expect(side.contains(el)).toBe(true);
    }
    for (let i = 0; i < order.length - 1; i++) {
      expect(isBefore(order[i], order[i + 1])).toBe(true);
    }
  });

  it("points at the user's own agent instead of offering to write the CV", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    // Decision 28 (slice A): Job360 has no LLM — no generate button anywhere.
    expect(screen.getByRole("heading", { name: /ask your assistant/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /tailor my cv/i })).toBeNull();
    expect(
      screen.queryByRole("heading", { name: /tailor my ats-friendly cv/i })
    ).toBeNull();
  });

  it("shows the next-step line under the company", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    expect(screen.getByTestId("next-step")).toHaveTextContent(
      "Next: CV ready — apply, then mark it applied"
    );
  });

  it("shows exactly one lesson event", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    expect(screen.getAllByTestId("lesson-here")).toHaveLength(1);
  });

  it("keeps History folded until the toggle is clicked", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    expect(screen.queryByTestId("timeline-event")).toBeNull();

    fireEvent.click(screen.getByTestId("history-toggle"));

    await waitFor(() =>
      expect(screen.getAllByTestId("timeline-event")).toHaveLength(1)
    );
  });
});

describe("ApplicationClient — Mark Applied asks first (decision 2)", () => {
  beforeEach(() => {
    getApplication.mockReset();
    getAlignment.mockReset();
    recordApplicationReceipt.mockReset();
    getApplication.mockResolvedValue({ ...detail(), status: "considering" });
    getAlignment.mockResolvedValue({
      fit: null,
      skills_in_ad: [],
      skills_not_in_ad: [],
      skills_total: 0,
      ad_chars: 0,
    });
    recordApplicationReceipt.mockResolvedValue({
      receipt_id: 1,
      sent_at: "2026-09-16T00:00:00Z",
      cv_artifact_id: 1,
      cv_version_no: 1,
      cover_letter_artifact_id: null,
      channel: "",
      confirmation: "",
      url: "/applications/42",
      event_id: 10,
    });
  });

  it("does not POST on the first click — only after Confirm", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    fireEvent.click(screen.getByTestId("mark-applied"));
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
    expect(
      screen.getByText(/record that you applied\? this creates a receipt/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("mark-applied-confirm"));

    await waitFor(() => expect(recordApplicationReceipt).toHaveBeenCalledWith(42, {}));
  });

  it("Cancel closes the confirm without posting", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    fireEvent.click(screen.getByTestId("mark-applied"));
    fireEvent.click(screen.getByTestId("mark-applied-cancel"));

    expect(screen.queryByTestId("mark-applied-confirm")).toBeNull();
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
  });
});

describe("ApplicationClient — status menu asks first (decision 1)", () => {
  beforeEach(() => {
    getApplication.mockReset();
    getAlignment.mockReset();
    recordApplicationEvent.mockReset();
    getApplication.mockResolvedValue({ ...detail(), status: "considering" });
    getAlignment.mockResolvedValue({
      fit: null,
      skills_in_ad: [],
      skills_not_in_ad: [],
      skills_total: 0,
      ad_chars: 0,
    });
    recordApplicationEvent.mockResolvedValue({
      event_id: 11,
      application_id: 42,
      event_type: "rejected",
      status: "rejected",
      status_changed: true,
    });
  });

  it("does not POST when an option is chosen — only after Confirm", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    fireEvent.change(screen.getByTestId("status-menu"), { target: { value: "rejected" } });
    expect(recordApplicationEvent).not.toHaveBeenCalled();
    expect(
      screen.getByText(/record "rejected"\? this is added to the history/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("status-confirm"));

    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(42, { event_type: "rejected" })
    );
  });

  it("Cancel closes the confirm without posting", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    fireEvent.change(screen.getByTestId("status-menu"), { target: { value: "rejected" } });
    fireEvent.click(screen.getByTestId("status-cancel"));

    expect(screen.queryByTestId("status-confirm")).toBeNull();
    expect(recordApplicationEvent).not.toHaveBeenCalled();
  });
});
