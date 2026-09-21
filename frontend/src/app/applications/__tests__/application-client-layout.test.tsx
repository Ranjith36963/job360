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

  it("orders sections fit -> documents -> sent -> people -> lessons -> timeline", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    const order = [
      "section-fit",
      "section-documents",
      "section-sent",
      "section-people",
      "section-lessons",
      "section-timeline",
    ].map((id) => screen.getByTestId(id));

    for (let i = 0; i < order.length - 1; i++) {
      expect(isBefore(order[i], order[i + 1])).toBe(true);
    }
  });

  it("points at the user's own agent instead of offering to write the CV", async () => {
    render(<ApplicationClient applicationId={42} />);
    await screen.findByText("Staff Engineer");

    // Decision 28 (slice A): Job360 has no LLM — no generate button anywhere.
    expect(screen.getByRole("heading", { name: /ask your agent/i })).toBeInTheDocument();
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
